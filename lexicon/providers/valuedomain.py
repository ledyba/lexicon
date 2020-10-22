"""Module provider for Value Domain"""
from __future__ import absolute_import

import hashlib
import json
import logging
import re
import requests
from urllib.parse import urlencode
from lexicon.providers.base import Provider as BaseProvider

LOGGER = logging.getLogger(__name__)

NAMESERVER_DOMAINS = ["value-domain.com", "dnsv.jp"]


def provider_parser(subparser):
    """Generate a provider parser for Value Domain"""
    subparser.add_argument(
        "--auth-token", help="specify access token for authentication"
    )


class Provider(BaseProvider):
    """Provider class for Value Domain"""

    def __init__(self, config):
        super(Provider, self).__init__(config)
        self.nameserver = "valuedomain1"
        self.ttl = 3600
        self.domain_id = None
        self.api_endpoint = "https://api.value-domain.com/v1"

    def _authenticate(self):
        payload = self._get("/domains/{0}/dns".format(self.domain))
        self.ttl = int(payload["results"]["ttl"])
        self.nameserver = payload["results"]["ns_type"]

        if payload["results"]["domainname"] == self.domain:
            self.domain_id = payload["results"]["domainid"]
            return

        raise Exception("No domain found")

    # Create record. If record already exists with the same content, do nothing'
    def _create_record(self, rtype, name, content):
        name = self._relative_name(name)
        resource_record_sets = self._get_resource_record_sets()
        index = self._find_resource_record_set(
            resource_record_sets, identifier=None, rtype=rtype, name=name, content=content
        )
        if index >= 0:
            LOGGER.debug("record already exists")
            return True

        resource_record_sets.append((rtype.lower(), name, self._bind_format_target(rtype, content)))
        self._update_resource_record_sets(resource_record_sets)
        LOGGER.debug("create_record: %s", True)
        return True

    @staticmethod
    def _identifier(record):
        sha256 = hashlib.sha256()
        sha256.update(('type=' + record[0].lower()).encode('utf-8'))
        sha256.update(('name=' + record[1]).encode('utf-8'))
        sha256.update(('data=' + record[2]).encode('utf-8'))
        return sha256.hexdigest()[0:7]

    # List all records. Return an empty list if no records found
    # type, name and content are used to filter records.
    # If possible filter during the query, otherwise filter after response is received.
    def _list_records(self, rtype=None, name=None, content=None):
        records = []
        full_name = None
        if name:
            full_name = self._full_name(name)

        for record in self._get_resource_record_sets():
            processed_record = {
                "type": record[0].upper(),
                "name": self._full_name(record[1]),
                "ttl": self.ttl,
                "content": record[2],
                'id': Provider._identifier(record),
            }
            if rtype and processed_record["type"].lower() != rtype.lower():
                continue
            if full_name and processed_record["name"] != full_name:
                continue
            if content and processed_record["content"] != content:
                continue
            records.append(processed_record)

        LOGGER.debug("list_records: %s", records)
        return records

    # Create or update a record.
    def _update_record(self, identifier=None, rtype=None, name=None, content=None):

        if not (rtype and name and content):
            raise Exception("rtype ,name and content must be specified.")

        name = self._relative_name(name)
        resource_record_sets = self._get_resource_record_sets()
        index = self._find_resource_record_set(
            resource_record_sets, identifier=identifier, rtype=rtype, name=name
        )

        record = (rtype.lower(), name, self._bind_format_target(rtype, content))
        if index >= 0:
            resource_record_sets[index] = record
        else:
            resource_record_sets.append(record)

        self._update_resource_record_sets(resource_record_sets)
        LOGGER.debug("create_record")

        LOGGER.debug("update_record: %s", True)
        return True

    # Delete an existing record.
    # If record does not exist, do nothing.
    def _delete_record(self, identifier=None, rtype=None, name=None, content=None):
        resource_record_sets = self._get_resource_record_sets()

        if name is not None:
            name = self._relative_name(name)
        if content is not None:
            content = self._bind_format_target(rtype, content)

        filtered_records = []
        for record in resource_record_sets:
            if identifier and Provider._identifier(record) != identifier:
                continue
            if rtype and record[0].lower() != rtype.lower():
                continue
            if name and record[1] != name:
                continue
            if content and record[2] != content:
                continue
            filtered_records.append(record)

        if not filtered_records:
            LOGGER.debug("delete_record: %s", False)
            return False

        for record in filtered_records:
            resource_record_sets.remove(record)

        self._update_resource_record_sets(resource_record_sets)
        LOGGER.debug("delete_record: %s", True)
        return True

    # Helpers
    def _full_name(self, record_name):
        if record_name == "@":
            record_name = self.domain
        return super(Provider, self)._full_name(record_name)

    def _relative_name(self, record_name):
        name = super(Provider, self)._relative_name(record_name)
        if not name:
            name = "@"
        return name

    def _bind_format_target(self, rtype, target):
        if rtype == "CNAME" and not target.endswith("."):
            target += "."
        return target

    def _find_resource_record_set(self, records, identifier=None, rtype=None, name=None, content=None):
        for index, record in enumerate(records):
            if identifier and Provider._identifier(record) == identifier:
                return index
            if rtype and record[0].lower() != rtype.lower():
                continue
            if name and record[1] != name:
                continue
            if content and record[2] != content:
                continue
            return index
        return -1

    def _get_resource_record_sets(self):
        payload = self._get("/domains/{0}/dns".format(self.domain))
        records = list(map(lambda line: tuple(re.split(r'\s+', line, 2)), payload["results"]["records"].splitlines()))
        return records

    def _update_resource_record_sets(self, resource_record_sets):
        record_txt = "\n".join(map(lambda rec: " ".join(rec), resource_record_sets))
        content = {
            "ns_type": self.nameserver,
            "records": record_txt,
            "ttl": self._get_lexicon_option("ttl"),
        }
        return self._put("/domains/{0}/dns".format(self.domain), content)

    def _request(self, action="GET", url="/", data=None, query_params=None):
        if data is None:
            data = {}
        if query_params is None:
            query_params = {}
        default_headers = {
            "Accept-Encoding": "identity",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer {0}".format(self._get_provider_option("auth_token")),
        }

        query_string = ""
        if query_params:
            query_string = urlencode(query_params)

        response = requests.request(
            action,
            self.api_endpoint + url,
            params=query_string,
            data=json.dumps(data),
            headers=default_headers,
        )
        try:
            # if the request fails for any reason, throw an error.
            response.raise_for_status()
        except BaseException:
            LOGGER.error(response.json().get("error_msg"))
            raise
        return response.json()