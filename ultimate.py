import asyncio
import logging
import json
import os
import random
import hashlib
import html
import time
from datetime import datetime
from aiohttp import ClientSession
from icmplib import ping as icmp_ping
import ntplib
import pytz
import statistics
from urllib.parse import parse_qs, urlparse, quote
import base64, requests

# --- Configuration ---
APPROVED_USERS_FILE = "approved_users.json"
USER_SESSIONS_FILE = "user_sessions.json"
TASKS_FILE = "tasks.json"
MICDATA_FILE = "micdata.json"

# --- Constants ---
USER_AGENT_STRING = "Mozilla/5.0 (Linux; Android 12; Mi 11) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Mobile Safari/537.36"
HEADERS = {"User-Agent": USER_AGENT_STRING}
BASE_URL = "https://account.xiaomi.com"
SID = "18n_bbs_global"
VERSION_CODE = "500423"
VERSION_NAME = "5.4.23"
BEIJING_TZ = pytz.timezone("Asia/Shanghai")

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")

# --- NTP and Server Lists ---
NTP_SERVERS = list(set([
    "time1.google.com", "time2.google.com", "time3.google.com", "time4.google.com",
    "time.android.com", "time.aws.com", "time.google.com", "time.cloudflare.com",
    "ntp.time.in.ua", "stratum1.net", "ntp5.stratum2.ru", "ntp.aliyun.com"
]))
MI_SERVERS = list(set(['sgp-api.buy.mi.com', '20.157.18.26']))

# --- JSON helpers ---
def load_json_file(filename, silent=False):
    if not os.path.exists(filename):
        return {}
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not silent:
            logging.info(f"Loaded {len(data) if isinstance(data, dict) else 'n/a'} entries from {filename}")
        return {str(k): v for k, v in data.items()} if isinstance(data, dict) else {}
    except (json.JSONDecodeError, FileNotFoundError) as e:
        if not silent:
            logging.warning(f"Could not load or parse {filename}: {e}. Starting empty.")
        return {}

# --- Login Logic from s4unlock4 ---
def parse_json_response(res):
    return json.loads(res.text[11:])

def login():
    user = input('\nEnter Mobile/ID: ')
    pwd  = input('Enter Password: ')
    hash_pwd = hashlib.md5(pwd.encode()).hexdigest().upper()
    cookies = {}

    r = requests.get(f"{BASE_URL}/pass/serviceLogin", params={'sid': SID, '_json': True}, cookies=cookies)
    cookies.update(r.cookies.get_dict()); deviceId=cookies["deviceId"]

    data = {k: v[0] for k,v in parse_qs(urlparse(parse_json_response(r)['location']).query).items()}
    data.update({'user': user,'hash': hash_pwd})

    r = requests.post(f"{BASE_URL}/pass/serviceLoginAuth2", data=data, cookies=cookies)
    res=parse_json_response(r);cookies.update(r.cookies.get_dict())
    if res["code"]==70016: exit("Invalid user/pass ❌")

    region = json.loads(requests.get(f"{BASE_URL}/pass/user/login/region",cookies=cookies).text[11:])["data"]["region"]
    nonce,ssecurity=res['nonce'],res['ssecurity']
    res['location']+=f"&clientSign={quote(base64.b64encode(hashlib.sha1(f'nonce={nonce}&{ssecurity}'.encode()).digest()))}"
    serviceToken=requests.get(res['location'],cookies=cookies).cookies.get_dict()

    micdata={"userId":res['userId'],"new_bbs_serviceToken":serviceToken["new_bbs_serviceToken"],"region":region,"deviceId":deviceId}
    with open(MICDATA_FILE,"w") as f: json.dump(micdata,f)
    return micdata

def load_account():
    try:
        with open(MICDATA_FILE) as f: d=json.load(f)
        if all(d.get(k) for k in ("userId","new_bbs_serviceToken","region","deviceId")):
            print(f"\nAccount ID: {d['userId']}")
            input("Press Enter to continue, Ctrl+D to logout...")
            return d
    except: pass
    if os.path.exists(MICDATA_FILE): os.remove(MICDATA_FILE)
    return login()

# --- Core Functions ---
def generate_device_id():
    random_data = f"{random.random()}-{time.time()}".encode('utf-8')
    return hashlib.sha1(random_data).hexdigest().upper()

async def get_beijing_time():
    client = ntplib.NTPClient()
    for server in random.sample(NTP_SERVERS, len(NTP_SERVERS)):
        try:
            response = await asyncio.to_thread(client.request, server, version=3)
            return datetime.fromtimestamp(response.tx_time, BEIJING_TZ)
        except Exception:
            continue
    logging.warning("NTP sync failed across all servers. Falling back to system time.")
    return datetime.now(BEIJING_TZ)

async def check_unlock_status(session, bbs_token: str, device_id: str):
    cookies = {"new_bbs_serviceToken": bbs_token, "deviceId": device_id, "versionCode": VERSION_CODE, "versionName": VERSION_NAME}
    url = "https://sgp-api.buy.mi.com/bbs/api/global/user/bl-switch/state"
    try:
        async with session.get(url, headers=HEADERS, cookies=cookies) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if data.get("code") == 100004:
                return "INVALID", "Token expired or invalid."

            status_data = data.get("data", {})
            if status_data.get("is_pass") == 4 and status_data.get("button_state") == 1:
                return "ELIGIBLE", "Account is eligible to unlock."

            deadline = status_data.get("deadline_format", "")
            error_map = {
                1: f"Application already approved until {deadline}.",
                2: f"Application blocked until {deadline}.",
                3: "Account is less than 30 days old."
            }
            return "INELIGIBLE", error_map.get(status_data.get('button_state'), 'Unknown account status.')
    except Exception as e:
        return "ERROR", f"Failed to check status: {html.escape(str(e))}"

async def post_unlock_apply(bbs_token: str, device_id: str):
    cookies = {"new_bbs_serviceToken": bbs_token, "deviceId": device_id, "versionCode": VERSION_CODE, "versionName": VERSION_NAME}
    async with ClientSession(cookies=cookies) as session:
        send_time = await get_beijing_time()
        async with session.post("https://sgp-api.buy.mi.com/bbs/api/global/apply/bl-auth",
                                headers=HEADERS, json={"is_retry": True}) as resp:
            receive_time = await get_beijing_time()
            resp.raise_for_status()
            return await resp.json(), send_time, receive_time

# Example main function for manual testing
async def main():
    micdata = load_account()  # <-- auto loads from micdata.json
    token = micdata["new_bbs_serviceToken"]
    device_id = micdata["deviceId"]
    async with ClientSession() as session:
        status, status_msg = await check_unlock_status(session, token, device_id)
        print(f"Status: {status} - {status_msg}")
        if status == "ELIGIBLE":
            result, send_time, receive_time = await post_unlock_apply(token, device_id)

            code = result.get("code")
            data = result.get("data", {})
            apply_result = data.get("apply_result")
            deadline = data.get("deadline_format", "")
            desc = result.get("msg", "")

            if code == 0 and apply_result == 1:
                print("✅ Application successful!")
            elif apply_result == 3:
                print(f"❌ Application limit reached. Try again on {deadline}.")
            else:
                print(f"❌ Unlock failed. Code: {code}, Result: {apply_result}, Desc: {desc}")

if __name__ == "__main__":
    asyncio.run(main())
