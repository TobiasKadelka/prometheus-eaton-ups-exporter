"""REST API web scraper for Eaton UPS measure data."""
import sys
import json

import urllib3
from requests import Session, Response
from requests.exceptions import SSLError, ConnectionError,\
    ReadTimeout, MissingSchema

from prometheus_eaton_ups_exporter.scraper_globals import LOGIN_AUTH_PATH, \
    REST_API_PATH, INPUT_MEMBER_ID, OUTPUT_MEMBER_ID, \
    LOGIN_DATA, LOGIN_TIMEOUT, REQUEST_TIMEOUT, \
    AUTHENTICATION_FAILED, SSL_ERROR, CERTIFICATE_VERIFY_FAILED,\
    CONNECTION_ERROR, TIMEOUT_ERROR, MISSING_SCHEMA


class UPSScraper:
    """
    Create a UPS Scraper based on the Eaton UPS's API.

    :param ups_address: str
        Address to a UPS, either an IP address or a DNS hostname
    :param authentication: (username: str, password: str)
        Username and password for the web UI of the UPS
    :param name: str
        Name of the UPS.
        Used as identifier to differentiate
        between multiple UPSs.
    :param insecure: bool
        Whether to connect to UPSs with self-signed SSL certificates

    """
    def __init__(self,
                 ups_address,
                 authentication,
                 name=None,
                 insecure=False):
        self.ups_address = ups_address
        self.username, self.password = authentication
        self.name = name
        self.session = Session()

        self.session.verify = not insecure  # ignore self signed certificate
        if not self.session.verify:
            # disable warnings created because of ignoring certificates
            urllib3.disable_warnings(
                urllib3.exceptions.InsecureRequestWarning
            )

        self.token_type, self.access_token = None, None

    def login(self) -> (str, str):
        """
        Login to the UPS Web UI.

        Based on analysing the UPS Web UI, this will create a POST request
        with the authentication details to successfully create a session
        on the specified UPS.

        :return: two for the authentication necessary string values
        """

        try:
            data = LOGIN_DATA
            data["username"] = self.username
            data["password"] = self.password

            login_request = self.session.post(
                self.ups_address + LOGIN_AUTH_PATH,
                data=json.dumps(data),  # needs to be JSON encoded
                timeout=LOGIN_TIMEOUT
            )
            login_response = login_request.json()

            token_type = login_response['token_type']
            access_token = login_response['access_token']

            print(f"Authentication successful on ({self.ups_address})")

            return token_type, access_token
        except KeyError:
            print("Authentication failed")
            sys.exit(AUTHENTICATION_FAILED)
        except SSLError as err:
            print("Connection refused")
            if 'CERTIFICATE_VERIFY_FAILED' in str(err):
                print("Try -k to allow insecure server "
                      "connections when using SSL")
                sys.exit(CERTIFICATE_VERIFY_FAILED)
            sys.exit(SSL_ERROR)
        except ConnectionError:
            print("Connection refused")
            sys.exit(CONNECTION_ERROR)
        except ReadTimeout:
            print(f"Login Timeout > {LOGIN_TIMEOUT} seconds")
            sys.exit(TIMEOUT_ERROR)

    def load_page(self, url) -> Response:
        """
        Load a webpage of the UPS Web UI or API.

        This will try to load the page by the given URL.
        If authentication is needed first, the login function gets executed
        before loading the specified page.

        :param url: ups web url
        :return: request.Response
        """

        try:
            headers = {
                "Connection": "keep-alive",
                "Authorization": f"{self.token_type} {self.access_token}",
            }
            request = self.session.get(
                url,
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )

            # Session might be expired, connect again
            try:
                if "errorCode" in request.json():
                    self.token_type, self.access_token = self.login()
                    return self.load_page(url)
            except ValueError:
                pass

            # try to login, if not authorized
            if "Unauthorized" in request.text:
                self.token_type, self.access_token = self.login()
                return self.load_page(url)

            return request
        except ConnectionError:
            self.token_type, self.access_token = self.login()
            return self.load_page(url)
        except ReadTimeout:
            print(f"Request Timeout > {REQUEST_TIMEOUT} seconds")
            sys.exit(TIMEOUT_ERROR)
        except MissingSchema as err:
            print(err)
            sys.exit(MISSING_SCHEMA)

    def get_measures(self) -> dict:
        """
        Get most relevant UPS metrics.

        :return: dict
        """
        power_dist_request = self.load_page(
            self.ups_address+REST_API_PATH
        )
        power_dist_overview = power_dist_request.json()

        if not self.name:
            self.name = f"ups_{power_dist_overview['id']}"

        ups_inputs_api = power_dist_overview['inputs']['@id']
        ups_ouptups_api = power_dist_overview['outputs']['@id']

        inputs_request = self.load_page(
            self.ups_address + ups_inputs_api + f'/{INPUT_MEMBER_ID}'
        )
        inputs = inputs_request.json()

        outputs_request = self.load_page(
            self.ups_address + ups_ouptups_api + f'/{OUTPUT_MEMBER_ID}'
        )
        outputs = outputs_request.json()

        ups_backup_sys_api = power_dist_overview['backupSystem']['@id']
        backup_request = self.load_page(
            self.ups_address + ups_backup_sys_api
        )
        backup = backup_request.json()
        ups_powerbank_api = backup['powerBank']['@id']
        powerbank_request = self.load_page(
            self.ups_address + ups_powerbank_api
        )
        powerbank = powerbank_request.json()

        return {
            "ups_id": self.name,
            "ups_inputs": inputs,
            "ups_outputs": outputs,
            "ups_powerbank": powerbank
        }
