import copy
import datetime
import json
import pathlib
import time
from http import HTTPStatus
from pathlib import Path
import httpx
import asyncio
import random


def parse_app_id(app_id):
    try:
        return int(str(app_id).strip())
    except ValueError:
        return None


def get_input_app_ids_filename() -> str:
    return "idlist.txt"


def app_id_reader(filename=None):
    if filename is None:
        filename = get_input_app_ids_filename()

    with Path(filename).open() as f:
        for row in f:
            yield parse_app_id(row)


def get_processed_app_ids_filename(filename_root="idprocessed"):
    current_date = time.strftime("%Y%m%d")
    return filename_root + "_on_" + current_date + ".txt"


def get_processed_app_ids():
    processed_app_ids_filename = get_processed_app_ids_filename()
    all_app_ids = set()
    try:
        for app_id in app_id_reader(processed_app_ids_filename):
            all_app_ids.add(app_id)
    except FileNotFoundError:
        print("Creating " + processed_app_ids_filename)
        pathlib.Path(processed_app_ids_filename).touch()
    return all_app_ids


def get_default_review_type() -> str:
    return "all"


def get_default_request_parameters(chosen_request_params=None):
    default_request_parameters = {
        "json": "1",
        "language": "all",
        "filter": "recent",
        "review_type": "all",
        "purchase_type": "all",
        "num_per_page": "100",
    }

    if chosen_request_params is not None:
        for element in chosen_request_params:
            default_request_parameters[element] = chosen_request_params[element]

    return default_request_parameters


def get_data_path():
    data_path = "data/"
    pathlib.Path(data_path).mkdir(parents=True, exist_ok=True)
    return data_path


def get_steam_api_url() -> str:
    return "https://store.steampowered.com/appreviews/"


def get_steam_api_rate_limits():
    return {
        "max_num_queries": 150,
        "cooldown": (5 * 60) + 10,
        "cooldown_bad_gateway": 10,
    }


def get_output_filename(app_id):
    return get_data_path() + "review_" + str(app_id) + ".json"


def get_dummy_query_summary():
    query_summary = {}
    query_summary["total_reviews"] = -1
    return query_summary


def load_review_dict(app_id):
    review_data_filename = get_output_filename(app_id)
    try:
        with Path(review_data_filename).open(encoding="utf8") as in_json_file:
            review_dict = json.load(in_json_file)
        if "cursors" not in review_dict:
            review_dict["cursors"] = {}
    except FileNotFoundError:
        review_dict = {}
        review_dict["reviews"] = {}
        review_dict["query_summary"] = get_dummy_query_summary()
        review_dict["cursors"] = {}

    return review_dict


def get_request(app_id, chosen_request_params=None):
    request = dict(get_default_request_parameters(chosen_request_params))
    request["appids"] = str(app_id)
    return request


async def fetch_with_proxies(client, url, params, proxies):
    for proxy in proxies:
        try:
            response = await client.get(url, params=params, proxies={"http://": proxy, "https://": proxy})
            if response.status_code == HTTPStatus.OK:
                return response
        except httpx.RequestError as e:
            print(f"Proxy {proxy} failed: {e}")
            continue
    raise RuntimeError("All proxies failed")


async def download_reviews_for_app_id_with_offset(
    app_id,
    query_count,
    cursor="*",
    chosen_request_params=None,
    proxies=None,
    semaphore=None,
):
    if semaphore:
        async with semaphore:
            return await _download_reviews_for_app_id_with_offset(
                app_id, query_count, cursor, chosen_request_params, proxies
            )
    else:
        return await _download_reviews_for_app_id_with_offset(
            app_id, query_count, cursor, chosen_request_params, proxies
        )


async def _download_reviews_for_app_id_with_offset(
    app_id,
    query_count,
    cursor="*",
    chosen_request_params=None,
    proxies=None,
):
    rate_limits = get_steam_api_rate_limits()
    req_data = get_request(app_id, chosen_request_params)
    req_data["cursor"] = str(cursor)
    url = get_steam_api_url() + req_data["appids"]

    async with httpx.AsyncClient() as client:
        try:
            response = await fetch_with_proxies(client, url, req_data, proxies)
            result = response.json()
        except httpx.HTTPStatusError as e:
            print(f"Faulty response status code: {e.response.status_code} for appID = {app_id} and cursor = {cursor}")
            result = {"success": 0}

    success_flag = bool(result["success"] == 1)

    try:
        downloaded_reviews = result["reviews"]
        query_summary = result["query_summary"]
        next_cursor = result["cursor"]
    except KeyError:
        success_flag = False
        downloaded_reviews = []
        query_summary = get_dummy_query_summary()
        next_cursor = cursor

    return success_flag, downloaded_reviews, query_summary, query_count, next_cursor


async def download_reviews_for_app_id(
    app_id,
    query_count=0,
    chosen_request_params=None,
    start_cursor="*",
    verbose=False,
    proxies=None,
    semaphore=None,
):
    rate_limits = get_steam_api_rate_limits()
    request = dict(get_default_request_parameters(chosen_request_params))
    check_review_timestamp = bool("day_range" in request and request["filter"] != "all")
    if check_review_timestamp:
        current_date = datetime.datetime.now(tz=datetime.UTC)
        num_days = int(request["day_range"])
        date_threshold = current_date - datetime.timedelta(days=num_days)
        timestamp_threshold = datetime.datetime.timestamp(date_threshold)
        if verbose:
            collection_keyword = "edited" if request["filter"] == "updated" else "first posted"
            print(f"Collecting reviews {collection_keyword} after {date_threshold}")

    review_dict = load_review_dict(app_id)
    previous_review_ids = set(review_dict["reviews"])
    num_reviews = None
    offset = 0
    cursor = start_cursor
    new_reviews = []
    new_review_ids = set()

    while (num_reviews is None) or (offset < num_reviews):
        if verbose:
            print(f"Cursor: {cursor}")

        success_flag, downloaded_reviews, query_summary, query_count, cursor = await download_reviews_for_app_id_with_offset(
            app_id, query_count, cursor, chosen_request_params, proxies, semaphore
        )

        delta_reviews = len(downloaded_reviews)
        offset += delta_reviews

        if success_flag and delta_reviews > 0:
            if check_review_timestamp:
                timestamp_str_field = "timestamp_updated" if request["filter"] == "updated" else "timestamp_created"
                checked_reviews = list(filter(lambda x: x[timestamp_str_field] > timestamp_threshold, downloaded_reviews))
                delta_checked_reviews = len(checked_reviews)

                if delta_checked_reviews == 0:
                    if verbose:
                        print("Exiting the loop to query Steam API, because the timestamp threshold was reached.")
                    break
                downloaded_reviews = checked_reviews

            new_reviews.extend(downloaded_reviews)
            downloaded_review_ids = [review["recommendationid"] for review in downloaded_reviews]

            if new_review_ids.issuperset(downloaded_review_ids):
                if verbose:
                    print("Exiting the loop to query Steam API, because this request only returned redundant reviews.")
                break

            new_review_ids = new_review_ids.union(downloaded_review_ids)

        else:
            if verbose:
                print("Exiting the loop to query Steam API, because this request failed.")
            break

        if num_reviews is None:
            if "total_reviews" not in query_summary:
                success_flag, query_summary, query_count = await download_the_full_query_summary(
                    app_id, query_count, chosen_request_params
                )

            review_dict["query_summary"] = query_summary
            num_reviews = query_summary["total_reviews"]
            print(f"[appID = {app_id}] expected #reviews = {num_reviews}")

        if query_count >= rate_limits["max_num_queries"]:
            cooldown_duration = rate_limits["cooldown"]
            print(f"Number of queries {query_count} reached. Cooldown: {cooldown_duration} seconds")
            await asyncio.sleep(cooldown_duration)
            query_count = 0

        if not previous_review_ids.isdisjoint(downloaded_review_ids):
            if verbose:
                print("Exiting the loop to query Steam API, because this request partially returned redundant reviews.")
            break

    review_dict["cursors"][str(cursor)] = time.asctime()

    for review in new_reviews:
        review_id = review["recommendationid"]
        if review_id not in previous_review_ids:
            review_dict["reviews"][review_id] = review

    with Path(get_output_filename(app_id)).open("w") as f:
        f.write(json.dumps(review_dict) + "\n")

    return review_dict, query_count


async def download_reviews_for_app_id_batch(
    input_app_ids=None,
    previously_processed_app_ids=None,
    chosen_request_params=None,
    verbose=False,
    proxies=None,
):
    if input_app_ids is None:
        print(f"Loading {get_input_app_ids_filename()}")
        input_app_ids = list(app_id_reader())

    if previously_processed_app_ids is None:
        print(f"Loading {get_processed_app_ids_filename()}")
        previously_processed_app_ids = get_processed_app_ids()

    # Set a semaphore limit for concurrency
    semaphore_limit = 10  # Adjust this number based on your requirements
    semaphore = asyncio.Semaphore(semaphore_limit)

    query_count = 0
    game_count = 0

    tasks = []
    for app_id in input_app_ids:
        if app_id in previously_processed_app_ids:
            print(f"Skipping previously found appID = {app_id}")
            continue

        print(f"Downloading reviews for appID = {app_id}")
        task = download_reviews_for_app_id(
            app_id,
            query_count,
            chosen_request_params,
            verbose=verbose,
            proxies=proxies,
            semaphore=semaphore
        )
        tasks.append(task)

    results = await asyncio.gather(*tasks)

    for review_dict, _ in results:
        game_count += 1
        app_id = review_dict.get("app_id", "unknown")
        with Path(get_processed_app_ids_filename()).open("a") as f:
            f.write(str(app_id) + "\n")
        num_downloaded_reviews = len(review_dict["reviews"])
        num_expected_reviews = review_dict["query_summary"]["total_reviews"]
        print(f"[appID = {app_id}] num_reviews = {num_downloaded_reviews} (expected: {num_expected_reviews})")

    print(f"Game records written: {game_count}")

    return True


if __name__ == "__main__":
    proxies = [
        "http://proxy1:port",
        "http://proxy2:port",
        "http://proxy3:port"
    ]

    asyncio.run(download_reviews_for_app_id_batch(
        input_app_ids=None,
        previously_processed_app_ids=None,
        verbose=False,
        proxies=proxies
    ))
