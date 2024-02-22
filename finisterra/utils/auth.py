import os
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import urllib.parse
import logging

logger = logging.getLogger('finisterra')


class AuthHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Override to prevent printing access logs to the console.
        pass

    def handle_error(self, request, client_address):
        # Override to prevent printing exceptions to the console.
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

        # Extract token from the path, for example, /?token=abc123
        url_path = self.path
        query_string = urllib.parse.urlparse(url_path).query
        query_dict = urllib.parse.parse_qs(query_string)
        token = query_dict.get('token', [None])[0]

        # Signal the script to continue with the token
        if token:
            os.environ['FT_API_TOKEN'] = token

        # Serve an HTML page indicating the window can be closed
        self.wfile.write(
            b"<html><body><p>Authentication successful. You can close this window.</p></body></html>")

        # Shutdown the HTTP server
        def shutdown_server():
            httpd.shutdown()

        threading.Thread(target=shutdown_server).start()


def start_server():
    global httpd
    server_address = ('', 8001)
    httpd = HTTPServer(server_address, AuthHandler)
    httpd.serve_forever()


def auth(payload):
    api_token = os.environ.get('FT_API_TOKEN')
    if not api_token:
        # Start local server in a separate thread
        server_thread = threading.Thread(target=start_server)
        server_thread.daemon = True
        server_thread.start()

        api_protocol = os.environ.get('FT_API_PROTOCOL_WEB', 'https')
        api_host = os.environ.get('FT_API_HOST_WEB', 'api.finisterra.io')
        api_port = os.environ.get('FT_API_PORT_WEB', '443')

        # Open the authentication URL in the default web browser
        auth_url = f"{api_protocol}://{api_host}:{api_port}/get-cli-token"
        logger.info(f"Opening the authentication URL: {auth_url}")
        webbrowser.open_new(auth_url)

        logger.info("Please authenticate in the opened web browser.")

        # Wait for the server thread to complete (i.e., until authentication is done)
        server_thread.join()

        # Check again for the token
        api_token = os.environ.get('FT_API_TOKEN')
        if not api_token:
            logger.error("Authentication failed or was cancelled.")
            exit()
