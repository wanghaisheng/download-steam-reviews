import argparse
import asyncio
import datetime
import httpx
import json
import re
import time
from bs4 import BeautifulSoup
from typing import List

class Scraper:
    def __init__(self, method, _url):
        self.method = method
        self._url = _url

    def get_url(self, **kwargs):
        return self._url.format(**kwargs, method=self.method)

    async def get_response(self, client):
        return await client.get(self.get_url())

    async def handle(self, response):
        return response.text

    async def scrape(self, client):
        response = await self.get_response(client)
        proxies = await self.handle(response)
        pattern = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?")
        return re.findall(pattern, proxies)

class SpysMeScraper(Scraper):
    def __init__(self, method):
        super().__init__(method, "https://spys.me/{mode}.txt")

    def get_url(self, **kwargs):
        mode = "proxy" if self.method == "http" else "socks" if self.method == "socks" else "unknown"
        if mode == "unknown":
            raise NotImplementedError
        return super().get_url(mode=mode, **kwargs)

class ProxyScrapeScraper(Scraper):
    def __init__(self, method, timeout=1000, country="All"):
        self.timeout = timeout
        self.country = country
        super().__init__(method,
                         "https://api.proxyscrape.com/?request=getproxies"
                         "&proxytype={method}"
                         "&timeout={timeout}"
                         "&country={country}")

    def get_url(self, **kwargs):
        return super().get_url(timeout=self.timeout, country=self.country, **kwargs)

class GeoNodeScraper(Scraper):
    def __init__(self, method, limit="500", page="1", sort_by="lastChecked", sort_type="desc"):
        self.limit = limit
        self.page = page
        self.sort_by = sort_by
        self.sort_type = sort_type
        super().__init__(method,
                         "https://proxylist.geonode.com/api/proxy-list?"
                         "&limit={limit}"
                         "&page={page}"
                         "&sort_by={sort_by}"
                         "&sort_type={sort_type}")

    def get_url(self, **kwargs):
        return super().get_url(limit=self.limit, page=self.page, sort_by=self.sort_by, sort_type=self.sort_type, **kwargs)

class ProxyListDownloadScraper(Scraper):
    def __init__(self, method, anon):
        self.anon = anon
        super().__init__(method, "https://www.proxy-list.download/api/v1/get?type={method}&anon={anon}")

    def get_url(self, **kwargs):
        return super().get_url(anon=self.anon, **kwargs)

class GeneralTableScraper(Scraper):
    async def handle(self, response):
        soup = BeautifulSoup(response.text, "html.parser")
        proxies = set()
        table = soup.find("table", attrs={"class": "table table-striped table-bordered"})
        if table:
            for row in table.findAll("tr"):
                count = 0
                proxy = ""
                for cell in row.findAll("td"):
                    if count == 1:
                        proxy += ":" + cell.text.replace("&nbsp;", "")
                        proxies.add(proxy)
                        break
                    proxy += cell.text.replace("&nbsp;", "")
                    count += 1
        return "\n".join(proxies)

class Downloadproxies:
    def __init__(self) -> None:
        with open("proxyserverlist.json", encoding="utf8") as file:
            self.api = json.load(file)
        self.proxy_dict = {"socks4": [], "socks5": [], "http": []}
        self.semaphore = asyncio.Semaphore(10)  # Limit concurrent requests

    async def fetch(self, url: str, client: httpx.AsyncClient) -> str:
        async with self.semaphore:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    async def get_special1(self):
        proxy_list = []
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                html = await self.fetch("https://www.socks-proxy.net/", client)
                part = html.split("<tbody>")[1].split("</tbody>")[0]
                part = part.split("<tr><td>")
                proxies = ""
                for proxy in part:
                    proxy = proxy.split("</td><td>")
                    try:
                        if proxies != "":
                            proxies += proxy[0] + ":" + proxy[1] + "\n"
                            if proxies != "":
                                proxy_list += proxies.split("\n")
                    except Exception as e:
                        print(f"Error processing proxy: {e}")
                        continue
            return proxy_list
        except httpx.RequestError as e:
            print(f"Request error: {e}")
            return []

    async def get_special2(self):
        async with httpx.AsyncClient(timeout=5) as client:
            proxies_online_response = await self.fetch("https://proxylist.geonode.com/api/proxy-summary", client)
            proxies_online = json.loads(proxies_online_response)["summary"]["proxiesOnline"]

            for i in range(proxies_online // 100):
                proxies_response = await self.fetch(f"https://proxylist.geonode.com/api/proxy-list?limit=100&page={i}&sort_by=lastChecked&sort_type=desc", client)
                proxies = json.loads(proxies_response)
                for p in proxies["data"]:
                    protocol = "http" if p["protocols"][0] == "https" else p["protocols"][0]
                    self.proxy_dict[protocol].append(f"{p['ip']}:{p['port']}")
        return

    async def get_extra(self):
        async with httpx.AsyncClient(timeout=5) as client:
            for q in range(20):
                self.count = {"http": 0, "socks5": 0}
                self.day = datetime.date.today() + datetime.timedelta(-q)
                url = f"https://checkerproxy.net/api/archive/{self.day.year}-{self.day.month}-{self.day.day}"
                try:
                    response = await self.fetch(url, client)
                    if response != "[]":
                        json_result = json.loads(response)
                        for i in json_result:
                            if re.match(r"172\.[0-9][0-9]\.0.1", i["ip"]):
                                if i["type"] in [1, 2] and i["addr"] in self.proxy_dict["http"]:
                                    self.proxy_dict["http"].remove(i["addr"])
                                if i["type"] == 4 and i["addr"] in self.proxy_dict["socks5"]:
                                    self.proxy_dict["socks5"].remove(i["addr"])
                            else:
                                if i["type"] in [1, 2]:
                                    self.count["http"] += 1
                                    self.proxy_dict["http"].append(i["addr"])
                                if i["type"] == 4:
                                    self.count["socks5"] += 1
                                    self.proxy_dict["socks5"].append(i["addr"])
                        print(f"> Get {self.count['http']} http proxy ips from {url}")
                        print(f"> Get {self.count['socks5']} socks5 proxy ips from {url}")
                except httpx.RequestError as e:
                    print(f"Request error: {e}")

        self.proxy_dict["socks4"] = list(set(self.proxy_dict["socks4"]))
        self.proxy_dict["socks5"] = list(set(self.proxy_dict["socks5"]))
        self.proxy_dict["http"] = list(set(self.proxy_dict["http"]))

        print("> Get extra proxies done")

    async def process_proxy_sources(self):
        await self.get_special1()
        # await self.get_special2()  # Uncomment if you want to include this source
        await self.get_extra()

        sys_proxies = {
            "http": "socks5h://127.0.0.1:1080",
            "https": "socks5h://127.0.0.1:1080",
        }

        async with httpx.AsyncClient(timeout=5) as client:
            for proxy_type in ["socks4", "socks5", "http"]:
                servers = self.api.get(proxy_type, [])
                servers = list(set(servers))
                for api in servers:
                    api = api.strip()
                    if " " in api:
                        api = api.replace(" ", "")
                    self.proxy_list = []
                    try:
                        response = await client.get(api, proxies=sys_proxies)
                        if response.status_code == httpx.codes.OK:
                            self.proxy_list += re.findall(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}", response.text)
                            self.proxy_dict[proxy_type] += list(set(self.proxy_list))
                            print(f"> Get {len(self.proxy_list)} {proxy_type} ips from {api}")
                    except httpx.RequestError as e:
                        print(f"Request error: {e}")

        print("> Get all proxies done")

    def save(self):
        for proxy_type in ["socks4", "socks5", "http"]:
            self.proxy_dict[proxy_type] = list(set(self.proxy_dict[proxy_type]))
            with open(f"{proxy_type}.txt", "w") as f:
                for i in self.proxy_dict[proxy_type]:
                    if "#" in i or i == "\n":
                        self.proxy_dict[proxy_type].remove(i)
                    else:
                        f.write(i + "\n")

            print(f"> Have already saved {len(self.proxy_dict[proxy_type])} proxies list as {proxy_type}.txt")

    def save_all(self):
        with open("all.txt", "w") as all_file:
            for proxy_type in self.proxy_dict:
                for proxy in self.proxy_dict[proxy_type]:
                    all_file.write(proxy + "\n")

        print("> Have already saved all proxies list as all.txt")

    async def scrape(self, method, output, verbose):
        now = time.time()
        methods = [method]
        if method == "socks":
            methods += ["socks4", "socks5"]

        scrapers = [
            SpysMeScraper("http"),
            SpysMeScraper("socks"),
            ProxyScrapeScraper("http"),
            ProxyScrapeScraper("socks4"),
            ProxyScrapeScraper("socks5"),
            GeoNodeScraper("socks"),
            ProxyListDownloadScraper("https", "elite"),
            ProxyListDownloadScraper("http", "elite"),
            ProxyListDownloadScraper("http", "transparent"),
            ProxyListDownloadScraper("http", "anonymous"),
            GeneralTableScraper("https", "http://sslproxies.org"),
            GeneralTableScraper("http", "http://free-proxy-list.net"),
            GeneralTableScraper("http", "http://us-proxy.org"),
            GeneralTableScraper("socks", "http://socks-proxy.net"),
        ]

        proxy_scrapers = [s for s in scrapers if s.method in methods]
        if not proxy_scrapers:
            raise ValueError("Method not supported")

        verbose_print(verbose, "Scraping proxies...")
        proxies = []

        tasks = []
        client = httpx.AsyncClient(follow_redirects=True)

        async def scrape_scraper(scraper):
            try:
                verbose_print(verbose, f"Looking {scraper.get_url()}...")
                proxies.extend(await scraper.scrape(client))
            except Exception as e:
                print(f"Error scraping {scraper.get_url()}: {e}")

        for scraper in proxy_scrapers:
            tasks.append(asyncio.ensure_future(scrape_scraper(scraper)))

        await asyncio.gather(*tasks)
        await client.aclose()

        verbose_print(verbose, f"Writing {len(proxies)} proxies to file...")
        with open(output, "w") as f:
            f.write("\n".join(proxies))
        verbose_print(verbose, "Done!")
        verbose_print(verbose, f"Took {time.time() - now} seconds")

async def main():
    d = Downloadproxies()
    await d.process_proxy_sources()
    d.save()
    d.save_all()

    # Example usage of the scraping functionality
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-p",
        "--proxy",
        help="Supported proxy type: " + ", ".join(sorted(set([s.method for s in scrapers]))),
        required=True,
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file name to save .txt file",
        default="output.txt",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="Increase output verbosity",
        action="store_true",
    )
    args = parser.parse_args()

    await d.scrape(args.proxy, args.output, args.verbose)

if __name__ == "__main__":
    asyncio.run(main())
