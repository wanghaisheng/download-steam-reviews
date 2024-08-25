import json
import datetime
import httpx
import re
import asyncio
from typing import List

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

async def main():
    d = Downloadproxies()
    await d.process_proxy_sources()
    d.save()
    d.save_all()

if __name__ == "__main__":
    asyncio.run(main())
