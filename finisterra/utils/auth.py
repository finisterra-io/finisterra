import os
import logging
import http.client
import json

logger = logging.getLogger('finisterra')

CREDENTIALS_FILE = os.path.expanduser('~/.finisterra/credentials.json')


def save_token_to_file(token):
    os.makedirs(os.path.dirname(CREDENTIALS_FILE), exist_ok=True)
    with open(CREDENTIALS_FILE, 'w') as file:
        json.dump({"credentials": {"app.finisterra.io": {"token": token}}}, file)


def read_token_from_file():
    try:
        with open(CREDENTIALS_FILE, 'r') as file:
            data = json.load(file)
            return data["credentials"]["app.finisterra.io"]["token"]
    except (FileNotFoundError, KeyError):
        return None


def delete_token_from_file():
    try:
        if os.path.exists(CREDENTIALS_FILE):
            with open(CREDENTIALS_FILE, 'r') as file:
                data = json.load(file)
            data["credentials"]["app.finisterra.io"]["token"] = ""
            with open(CREDENTIALS_FILE, 'w') as file:
                json.dump(data, file)
    except Exception as e:
        logger.error(f"Failed to delete token from file: {e}")


def prompt_for_token(auth_url):
    print("\033[1;96mPlease authenticate by visiting the following URL:\033[0m")
    print(auth_url)
    print("\033[1;96mAfter obtaining the token, please enter it below:\033[0m")
    return input("Token: ")


def get_auth_url():
    api_protocol = os.environ.get('FT_API_PROTOCOL_WEB', 'https')
    api_host = os.environ.get('FT_API_HOST_WEB', 'app.finisterra.io')
    api_port = os.environ.get('FT_API_PORT_WEB', '443')
    api_part = os.environ.get('FT_API_PART_WEB', 'organization/apikeys')
    return f"{api_protocol}://{api_host}:{api_port}/{api_part}"


def auth(payload):
    api_token = os.environ.get('FT_API_TOKEN')
    if not api_token:
        api_token = read_token_from_file()

    if not api_token:
        auth_url = get_auth_url()
        api_token = prompt_for_token(auth_url)
        if api_token:
            os.environ['FT_API_TOKEN'] = api_token
            save_token_to_file(api_token)
        else:
            logger.error("No token provided.")
            exit()

    api_host = os.environ.get('FT_API_HOST', 'api.finisterra.io')
    api_port = os.environ.get('FT_API_PORT', 443)
    api_path = '/auth/'
    logger.debug(f"Authenticating with {api_host}:{api_port}...")
    conn = http.client.HTTPSConnection(
        api_host, api_port) if api_port == 443 else http.client.HTTPConnection(api_host, api_port)
    headers = {'Content-Type': 'application/json',
               "Authorization": "Bearer " + api_token}
    payload_json = json.dumps(payload, default=list)
    logger.debug("Validating token...")
    conn.request('POST', api_path, body=payload_json, headers=headers)
    response = conn.getresponse()

    if response.status == 200:
        return True  # Authentication successful
    else:
        logger.error(f"Error: {response.status} - {response.reason}")
        delete_token_from_file()
        exit(-1)
