import asyncio
import logging
import json
import os
import random
import hashlib
import base64
import html
import time
from urllib.parse import parse_qs, urlparse, quote
from datetime import datetime, timedelta
from aiohttp import ClientSession
from icmplib import ping as icmp_ping
import ntplib
import pytz
import statistics
import requests

# --- Configuration ---
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
NTP_SERVERS = [
    "time1.google.com", "time2.google.com", "time3.google.com", "time4.google.com",
    "time.android.com", "time.aws.com", "time.google.com", "time.cloudflare.com",
    "ntp.aliyun.com"
]
MI_SERVERS = ['sgp-api.buy.mi.com', '20.157.18.26']

# --- Login Logic ---
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

# --- Time helpers ---
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

# --- Ping & Latency ---
def _test_icmp_ping_sync():
    pings = []
    for host in random.sample(MI_SERVERS, len(MI_SERVERS)):
        try:
            res = icmp_ping(host, count=3, interval=0.2, timeout=1)
            if res.is_alive:
                pings.append(res.avg_rtt)
        except Exception:
            continue
    return round(statistics.mean(pings), 2) if pings else 10.0

def calculate_script_time(ping_ms):
    if ping_ms <= 0:
        ping_ms = 1
    result = 59.975 if ping_ms <= 5 else 59.091 + (166 - ping_ms) * 0.006
    return max(55.0, min(65.0, result))

# --- API Functions ---
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
        print(f"🚀 Sending request at: {send_time.strftime('%Y-%m-%d %H:%M:%S.%f')} (UTC+8)")

        async with session.post("https://sgp-api.buy.mi.com/bbs/api/global/apply/bl-auth",
                                headers=HEADERS, json={"is_retry": True}) as resp:
            receive_time = await get_beijing_time()
            print(f"✅ Response received at: {receive_time.strftime('%Y-%m-%d %H:%M:%S.%f')} (UTC+8)")
            resp.raise_for_status()
            return await resp.json(), send_time, receive_time

# --- Scheduler ---
async def schedule_task(micdata):
    token = micdata["new_bbs_serviceToken"]
    device_id = micdata["deviceId"]

    async with ClientSession() as session:
        status, status_msg = await check_unlock_status(session, token, device_id)
        if status != "ELIGIBLE":
            print(f"❌ Status: {status_msg}")
            return
        print("✅ Account is eligible to unlock.")

    now = await get_beijing_time()
    target_ping_start = now.replace(hour=23, minute=59, second=48, microsecond=0)
    if now > target_ping_start:
        target_ping_start += timedelta(days=1)

    print(f"🌐 Synchronizing time with NTP...")
    print(f"🕒 Beijing Time: {now.strftime('%Y-%m-%d %H:%M:%S.%f')}")
    print(f"🕒 Waiting until {target_ping_start.strftime('%H:%M:%S')} CST to begin ping measurement...")

    await asyncio.sleep((target_ping_start - await get_beijing_time()).total_seconds())

    print("🎯 Target time reached. Starting ping calculation...")
    avg_ping = await asyncio.to_thread(_test_icmp_ping_sync)
    script_time_s = calculate_script_time(avg_ping)

    now_after_ping = await get_beijing_time()
    base = now_after_ping.replace(hour=23, minute=59, second=0, microsecond=0)
    target_apply_time = base + timedelta(seconds=script_time_s)
    if now_after_ping > target_apply_time:
        target_apply_time += timedelta(days=1)

    print(f"📈 Average ping: {avg_ping} ms")
    print(f"🧠 Calculated apply time: {script_time_s:.3f} s")
    print(f"⏳ Waiting until {target_apply_time.strftime('%H:%M:%S.%f')} CST to apply...")

    await asyncio.sleep((target_apply_time - await get_beijing_time()).total_seconds())

    try:
        result, send_time, receive_time = await post_unlock_apply(token, device_id)
    except Exception as e:
        print(f"❌ Error during request: {html.escape(str(e))}")
        return

    code = result.get("code")
    data = result.get("data", {})
    apply_result = data.get("apply_result")
    desc = result.get('desc', '')
    deadline = data.get("deadline_format", "")

    if code == 0 and apply_result == 1:
        print("✅ Status: Congratulations! Your application was successful.")
    elif apply_result == 3:
        print(f"❌ Status: Application not submitted, limit reached ({desc}), try again on {deadline}.")
    else:
        print(f"❌ Status: Unlock failed. Code: {code}, Result: {apply_result}, Desc: {html.escape(desc)}")

# --- Main ---
async def main():
    print("~~~V[•ULTIMATE•]~~~\nAutomated Bootloader Unlock Permission\nCredits:-\n        Enhanced by Chatgpt\n        Co-Devs @suchit-repo, @miservice")
    micdata = load_account()
    await schedule_task(micdata)

if __name__ == "__main__":
    asyncio.run(main())
