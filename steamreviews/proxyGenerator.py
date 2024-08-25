import asyncio
from typing import Dict, Optional, Callable
from fp.fp import FreeProxy
import random
import logging
import httpx
import tempfile
from deprecated import deprecated

try:
    import stem.process
    from stem import Signal
    from stem.control import Controller
except ImportError:
    stem = None

try:
    from fake_useragent import UserAgent
    FAKE_USERAGENT = True
except Exception:
    FAKE_USERAGENT = False
    DEFAULT_USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.149 Safari/537.36'

from .data_types import ProxyMode

class DOSException(Exception):
    """DOS attack was detected."""

class MaxTriesExceededException(Exception):
    """Maximum number of tries by scholarly reached"""

class ProxyGenerator:
    def __init__(self):
        self.logger = logging.getLogger('scholarly')

        self._proxy_gen = None
        self._proxy_works = False
        self.proxy_mode = None
        self._proxies = {}
        self._tor_process = None
        self._can_refresh_tor = False
        self._tor_control_port = None
        self._tor_password = None
        self._session = None
        self._TIMEOUT = 5
        self._sem = asyncio.Semaphore(5)  # Limit to 5 concurrent checks
        self._new_session()

    def __del__(self):
        if self._tor_process:
            self._tor_process.kill()
            self._tor_process.wait()
        self._close_session()

    def get_session(self):
        return self._session

    def Luminati(self, usr, passwd, proxy_port):
        """Set up a Luminati proxy."""
        if usr and passwd and proxy_port:
            proxy = f"http://{usr}-session-{random.random()}:{passwd}@zproxy.lum-superproxy.io:{proxy_port}"
            if asyncio.run(self._use_proxy(proxy, proxy)):
                self.logger.info("Luminati proxy setup successfully")
                self.proxy_mode = ProxyMode.LUMINATI
            else:
                self.logger.warning("Luminati proxy does not seem to work.")
            return self._proxy_works
        self.logger.warning("Not enough parameters provided for Luminati proxy.")
        return False

    def SingleProxy(self, http=None, https=None):
        """Use a single proxy."""
        if http[:4] != "http":
            http = "http://" + http
        if https is None:
            https = http
        elif https[:5] != "https":
            https = "https://" + https

        proxies = {'http://': http, 'https://': https}
        if asyncio.run(self._use_proxy(http, https)):
            self.proxy_mode = ProxyMode.SINGLEPROXY
            self.logger.info("Proxy setup successfully")
        else:
            self.logger.warning("Unable to setup the proxy.")
        return self._proxy_works

    async def _check_proxy(self, proxies: Dict[str, str]) -> bool:
        """Check if a proxy is working."""
        try:
            async with httpx.AsyncClient(proxies=proxies, timeout=self._TIMEOUT) as client:
                resp = await client.get("http://httpbin.org/ip")
                if resp.status_code == 200:
                    self.logger.info("Proxy works! IP address: %s", resp.json()["origin"])
                    return True
                elif resp.status_code == 401:
                    self.logger.warning("Incorrect credentials for proxy!")
                    return False
        except httpx.RequestError as e:
            self.logger.debug("Exception while testing proxy: %s", e)
            if self.proxy_mode in (ProxyMode.LUMINATI, ProxyMode.SCRAPERAPI):
                self.logger.warning("Double check your credentials and try increasing the timeout")
        return False

    async def _refresh_tor_id(self, tor_control_port: int, password: str) -> bool:
        """Refreshes the ID by using a new Tor node."""
        try:
            async with Controller.from_port(port=tor_control_port) as controller:
                if password:
                    controller.authenticate(password=password)
                else:
                    controller.authenticate()
                controller.signal(Signal.NEWNYM)
                self._new_session()
            return True
        except Exception as e:
            self.logger.info(f"Exception {e} while refreshing TOR. Retrying...")
            return False

    async def _use_proxy(self, http: str, https: str = None) -> bool:
        """Allows user to set their own proxy for the connection session."""
        if http[:4] != "http":
            http = "http://" + http
        if https is None:
            https = http
        elif https[:5] != "https":
            https = "https://" + https

        proxies = {'http://': http, 'https://': https}
        if self.proxy_mode == ProxyMode.SCRAPERAPI:
            response = await httpx.get("http://api.scraperapi.com/account", params={'api_key': self._API_KEY})
            r = response.json()
            if "error" in r:
                self.logger.warning(r["error"])
                self._proxy_works = False
            else:
                self._proxy_works = r["requestCount"] < int(r["requestLimit"])
                self.logger.info("Successful ScraperAPI requests %d / %d", r["requestCount"], r["requestLimit"])
        else:
            self._proxy_works = await self._check_proxy(proxies)

        if self._proxy_works:
            self._proxies = proxies
            self._new_session(proxies=proxies)

        return self._proxy_works

    @deprecated(version='1.5', reason="Tor methods are deprecated and are not actively tested.")
    def Tor_External(self, tor_sock_port: int, tor_control_port: int, tor_password: str):
        """Setting up Tor Proxy."""
        if stem is None:
            raise RuntimeError("Tor methods are not supported with the basic version of the package. "
                               "Please install scholarly[tor] to use this method.")

        self._TIMEOUT = 10

        proxy = f"socks5://127.0.0.1:{tor_sock_port}"
        if asyncio.run(self._use_proxy(proxy, proxy)):
            self._can_refresh_tor = await self._refresh_tor_id(tor_control_port, tor_password)
            if self._can_refresh_tor:
                self._tor_control_port = tor_control_port
                self._tor_password = tor_password
            else:
                self._tor_control_port = None
                self._tor_password = None
            self.proxy_mode = ProxyMode.TOR

    async def Tor_Internal(self, tor_dir: str, tor_control_port: int, tor_password: str):
        """Setting up an internal Tor Proxy."""
        if stem is None:
            raise RuntimeError("Tor methods are not supported with the basic version of the package. "
                               "Please install scholarly[tor] to use this method.")

        self._TIMEOUT = 10
        # Start Tor process
        self._tor_process = stem.process.launch_tor_with_config(
            tor_cmd="tor",
            config={
                'ControlPort': str(tor_control_port),
                'DataDirectory': tempfile.mkdtemp(),
                'SocksPort': '9050',
            },
            password=tor_password,
        )
        self._can_refresh_tor = True
        self.proxy_mode = ProxyMode.TOR
        proxy = f"socks5://127.0.0.1:9050"
        if asyncio.run(self._use_proxy(proxy, proxy)):
            self._tor_control_port = tor_control_port
            self._tor_password = tor_password

    def _new_session(self, proxies=None):
        """Initializes a new HTTPX session with optional proxies."""
        self._session = httpx.AsyncClient(proxies=proxies, timeout=self._TIMEOUT)

    def _close_session(self):
        """Closes the current HTTPX session."""
        if self._session:
            asyncio.run(self._session.aclose())

    async def FreeProxies(self, timeout=5, wait_time=300):
        """Use a free proxy from a given proxy provider."""
        free_proxy = FreeProxy(timeout=timeout, rand=True)
        proxy = free_proxy.get()
        if await self._use_proxy(proxy, proxy):
            self.logger.info("Successfully set up free proxy: %s", proxy)
            self.proxy_mode = ProxyMode.FREE
        else:
            self.logger.warning("Failed to set up free proxy.")
        return self._proxy_works

    async def check_proxies_concurrently(self, proxies_list: list):
        """Check multiple proxies concurrently using semaphore."""
        async def check_proxy(proxy):
            async with self._sem:
                proxies = {'http://': proxy, 'https://': proxy}
                if await self._check_proxy(proxies):
                    return proxy
                return None

        tasks = [check_proxy(proxy) for proxy in proxies_list]
        results = await asyncio.gather(*tasks)
        return [proxy for proxy in results if proxy]

# Example usage
async def main():
    pg = ProxyGenerator()

    # Example of using FreeProxies method to set up proxies
    await pg.FreeProxies(timeout=5, wait_time=300)

    # Example list of proxies to check
    proxies_list = ['http://proxy1.com', 'http://proxy2.com', 'http://proxy3.com']
    working_proxies = await pg.check_proxies_concurrently(proxies_list)
    print(f"Working proxies: {working_proxies}")

if __name__ == "__main__":
    asyncio.run(main())
