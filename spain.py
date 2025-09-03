import subprocess import sys import os import platform

List of NTP servers

ntp_servers = [ "ntp0.ntp-servers.net", "ntp1.ntp-servers.net", "ntp2.ntp-servers.net", "ntp3.ntp-servers.net", "ntp4.ntp-servers.net", "ntp5.ntp-servers.net", "ntp6.ntp-servers.net" ]

MI_SERVERS = ['161.117.96.161', '20.157.18.26']

Install dependencies

def install_package(package): subprocess.check_call([sys.executable, "-m", "pip", "install", package])

required_packages = ["requests", "ntplib", "pytz", "urllib3", "icmplib", "colorama", "linecache"] for package in required_packages: try: import(package) except ImportError: print(f"Installing package {package}...") install_package(package)

os.system('cls' if os.name == 'nt' else 'clear')

import hashlib import linecache import random import time from datetime import datetime, timezone, timedelta import ntplib import pytz import urllib3 import json import statistics from icmplib import ping from colorama import init, Fore, Style

Color configuration

init(autoreset=True) col_g = Fore.GREEN col_gb = Style.BRIGHT + Fore.GREEN col_b = Fore.BLUE col_bb = Style.BRIGHT + Fore.BLUE col_y = Fore.YELLOW col_yb = Style.BRIGHT + Fore.YELLOW col_r = Fore.RED col_rb = Style.BRIGHT + Fore.RED

Version and token number

token_number = int(input(col_g + f"[Token line number]: " + Fore.RESET)) os.system('cls' if os.name == 'nt' else 'clear') scriptversion = "ARU_FHL_v070425"

Global variables

print(col_yb + f"{scriptversion}token#{token_number}:") print(col_y + f"Checking account status" + Fore.RESET) token = linecache.getline("token.txt" , token_number).strip() cookie_value = token feedtime = float(linecache.getline("timeshift.txt" , token_number).strip()) feed_time_shift = feedtime feed_time_shift_1 = feed_time_shift / 1000

Generate unique device ID

def generate_device_id(): random_data = f"{random.random()}-{time.time()}" device_id = hashlib.sha1(random_data.encode('utf-8')).hexdigest().upper() return device_id

Get initial Beijing time from NTP

def get_initial_beijing_time(): client = ntplib.NTPClient() beijing_tz = pytz.timezone("Asia/Shanghai") for server in ntp_servers: try: print(col_y + f"\nGetting current Beijing time" + Fore.RESET) response = client.request(server, version=3) ntp_time = datetime.fromtimestamp(response.tx_time, timezone.utc) beijing_time = ntp_time.astimezone(beijing_tz) print(col_g + f"[Beijing Time]: " + Fore.RESET +  f"{beijing_time.strftime('%Y-%m-%d %H:%M:%S.%f')}") return beijing_time except Exception as e: print(f"Error connecting to {server}: {e}") print(f"Could not connect to any NTP server.") return None

Sync Beijing time

def get_synchronized_beijing_time(start_beijing_time, start_timestamp): elapsed = time.time() - start_timestamp current_time = start_beijing_time + timedelta(seconds=elapsed) return current_time

Wait until target unlock time

def wait_until_target_time(start_beijing_time, start_timestamp): next_day = start_beijing_time + timedelta(days=1) print(col_y + f"\nBootloader unlock request" + Fore.RESET) print(col_g + f"[Offset set]: " + Fore.RESET + f"{feed_time_shift:.2f} ms.") target_time = next_day.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=feed_time_shift_1) print(col_g + f"[Waiting until]: " + Fore.RESET + f"{target_time.strftime('%Y-%m-%d %H:%M:%S.%f')}") print(f"Do not close this window...")

while True:
    current_time = get_synchronized_beijing_time(start_beijing_time, start_timestamp)
    time_diff = target_time - current_time
    
    if time_diff.total_seconds() > 1:
        time.sleep(min(1.0, time_diff.total_seconds() - 1))
    elif current_time >= target_time:
        print(f"Target time reached: {current_time.strftime('%Y-%m-%d %H:%M:%S.%f')}. Starting request sending...")
        break
    else:
        time.sleep(0.0001)

Check account unlock status via API

def check_unlock_status(session, cookie_value, device_id): try: url = "https://sgp-api.buy.mi.com/bbs/api/global/user/bl-switch/state" headers = { "Cookie": f"new_bbs_serviceToken={cookie_value};versionCode=500411;versionName=5.4.11;deviceId={device_id};" }

response = session.make_request('GET', url, headers=headers)
    if response is None:
        print(f"[Error] Could not get unlock status.")
        return False

    response_data = json.loads(response.data.decode('utf-8'))
    response.release_conn()

    if response_data.get("code") == 100004:
        print(f"[Error] Cookie expired, needs updating.")
        input(f"Press Enter to close...")
        exit()

    data = response_data.get("data", {})
    is_pass = data.get("is_pass")
    button_state = data.get("button_state")
    deadline_format = data.get("deadline_format", "")

    if is_pass == 4:
        if button_state == 1:
            print(col_g + f"[Account Status]: " + Fore.RESET + f"Request can be sent.")
            return True

        elif button_state == 2:
            print(col_g + f"[Account Status]: " + Fore.RESET + f"Blocked from sending requests until {deadline_format} (MM/DD).")
            status_2 = (input(f"Continue (" + col_b + f"Yes/No" +Fore.RESET + f")?: ") )
            if status_2.lower() in ['y', 'yes']:
                return True
            else:
                input(f"Press Enter to close...")
                exit()
        elif button_state == 3:
            print(col_g + f"[Account Status]: " + Fore.RESET + f"Account created less than 30 days ago.")
            status_3 = (input(f"Continue (" + col_b + f"Yes/No" +Fore.RESET + f")?: ") )
            if status_3.lower() in ['y', 'yes']:
                return True
            else:
                input(f"Press Enter to close...")
                exit()
    elif is_pass == 1:
        print(col_g + f"[Account Status]: " + Fore.RESET + f"Request approved, unlock possible until {deadline_format}.")
        input(f"Press Enter to close...")
        exit()
    else:
        print(col_g + f"[Account Status]: " + Fore.RESET + f"Unknown status.")
        input(f"Press Enter to close...")
        exit()
except Exception as e:
    print(f"[Error checking status] {e}")
    return False

HTTP request container

class HTTP11Session: def init(self): self.http = urllib3.PoolManager( maxsize=10, retries=True, timeout=urllib3.Timeout(connect=2.0, read=15.0), headers={} )

def make_request(self, method, url, headers=None, body=None):
    try:
        request_headers = {}
        if headers:
            request_headers.update(headers)
            request_headers['Content-Type'] = 'application/json; charset=utf-8'
        
        if method == 'POST':
            if body is None:
                body = '{"is_retry":true}'.encode('utf-8')
            request_headers['Content-Length'] = str(len(body))
            request_headers['Accept-Encoding'] = 'gzip, deflate, br'
            request_headers['User-Agent'] = 'okhttp/4.12.0'
            request_headers['Connection'] = 'keep-alive'
        
        response = self.http.request(
            method,
            url,
            headers=request_headers,
            body=body,
            preload_content=False
        )
        
        return response
    except Exception as e:
        print(f"[Network Error] {e}")
        return None

def main():

device_id = generate_device_id()
session = HTTP11Session()

if check_unlock_status(session, cookie_value, device_id):
    start_beijing_time = get_initial_beijing_time()
    if start_beijing_time is None:
        print(f"Could not set initial time. Press Enter to close...")
        input()
        exit()

    start_timestamp = time.time()
    
    wait_until_target_time(start_beijing_time, start_timestamp)

    url = "https://sgp-api.buy.mi.com/bbs/api/global/apply/bl-auth"
    headers = {
        "Cookie": f"new_bbs_serviceToken={cookie_value};versionCode=500411;versionName=5.4.11;deviceId={device_id};"
    }

    try:
        while True:
            request_time = get_synchronized_beijing_time(start_beijing_time, start_timestamp)
            print(col_g + f"[Request]: " + Fore.RESET + f"Sending request at {request_time.strftime('%Y-%m-%d %H:%M:%S.%f')} (UTC+8)")
            
            response = session.make_request('POST', url, headers=headers)
            if response is None:
                continue

            response_time = get_synchronized_beijing_time(start_beijing_time, start_timestamp)
            print(col_g + f"[Response]: " + Fore.RESET + f"Response received at {response_time.strftime('%Y-%m-%d %H:%M:%S.%f')} (UTC+8)")

            try:
                response_data = response.data
                response.release_conn()
                json_response = json.loads(response_data.decode('utf-8'))
                code = json_response.get("code")
                data = json_response.get("data", {})

                if code == 0:
                    apply_result = data.get("apply_result")
                    if apply_result == 1:
                        print(col_g + f"[Status]: " + Fore.RESET + f"Request approved, checking status...")
                        check_unlock_status(session, cookie_value, device_id)
                    elif apply_result == 3:
                        deadline_format = data.get("deadline_format", "Not specified")
                        print(col_g + f"[Status]: " + Fore.RESET + f"Request not sent, limit reached. Try again on {deadline_format} (MM/DD).")
                        input(f"Press Enter to close...")
                        exit()
                    elif apply_result == 4:
                        deadline_format = data.get("deadline_format", "Not specified")
                        print(col_g + f"[Status]: " + Fore.RESET + f"Request not sent, blocked until {deadline_format} (MM/DD).")
                        input(f"Press Enter to close...")
                        exit()
                elif code == 100001:
                    print(col_g + f"[Status]: " + Fore.RESET + f"Request rejected, error in request.")
                    print(col_g + f"[Full Response]: " + Fore.RESET + f"{json_response}")
                elif code == 100003:
                    print(col_g + f"[Status]: " + Fore.RESET + f"Request may have been approved, checking status...")
                    print(col_g + f"[Full Response]: " + Fore.RESET + f"{json_response}")
                    check_unlock_status(session, cookie_value, device_id)
                elif code is not None:
                    print(col_g + f"[Status]: " + Fore.RESET + f"Unknown request status: {code}")
                    print(col_g + f"[Full Response]: " + Fore.RESET + f"{json_response}")
                else:
                    print(col_g + f"[Error]: " + Fore.RESET + f"Response did not contain required code.")
                    print(col_g + f"[Full Response]: " + Fore.RESET + f"{json_response}")

            except json.JSONDecodeError:
                print(col_g + f"[Error]: " + Fore.RESET + f"Could not decode JSON response.")
                print(col_g + f"[Server Response]: " + Fore.RESET + f"{response_data}")
            except Exception as e:
                print(col_g + f"[Error processing response]: " + Fore.RESET + f"{e}")
                continue

    except Exception as e:
        print(col_g + f"[Request Error]: " + Fore.RESET + f"{e}")
        input(f"Press Enter to close...")
        exit()

if name == "main": main()

