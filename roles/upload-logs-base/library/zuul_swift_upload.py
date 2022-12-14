# Copyright 2014 Rackspace Australia
# Copyright 2018 Red Hat, Inc
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

# Make coding more python3-ish
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


"""
Utility to upload files to swift

Run this from the CLI from the zuul-jobs/roles directory with:

  python -m upload-logs-base.library.zuul_swift_upload
"""

import argparse
import logging
import os
try:
    import queue as queuelib
except ImportError:
    import Queue as queuelib
import sys
import threading
import traceback

import openstack
import requests
import requests.exceptions
import requestsexceptions
import keystoneauth1.exceptions

from ansible.module_utils.basic import AnsibleModule

try:
    # Ansible context
    from ansible.module_utils.zuul_jobs.upload_utils import (
        FileList,
        GzipFilter,
        Indexer,
        retry_function,
    )
except ImportError:
    # Test context
    from ..module_utils.zuul_jobs.upload_utils import (
        FileList,
        GzipFilter,
        Indexer,
        retry_function,
    )

MAX_UPLOAD_THREADS = 24


def get_cloud(cloud):
    if isinstance(cloud, dict):
        config = openstack.config.loader.OpenStackConfig().get_one(**cloud)
        return openstack.connection.Connection(config=config)
    else:
        return openstack.connect(cloud=cloud)


class Uploader():
    def __init__(self, cloud, container, prefix=None, delete_after=None,
                 public=True, dry_run=False):

        self.dry_run = dry_run
        if dry_run:
            self.url = 'http://dry-run-url.com/a/path/'
            return

        self.cloud = cloud
        self.container = container
        self.prefix = prefix or ''
        self.delete_after = delete_after

        sess = self.cloud.config.get_session()
        adapter = requests.adapters.HTTPAdapter(pool_maxsize=100)
        sess.mount('https://', adapter)

        # If we're in Rackspace, there's some non-standard stuff we
        # need to do to get the public endpoint.
        try:
            cdn_endpoint = self.cloud.session.auth.get_endpoint(
                self.cloud.session, service_type='rax:object-cdn',
                region_name=self.cloud.config.region_name,
                interface=self.cloud.config.interface)
            cdn_url = os.path.join(cdn_endpoint, self.container)
        except keystoneauth1.exceptions.catalog.EndpointNotFound:
            cdn_url = None

        # We retry here because sometimes we get HTTP 401 errors in rax.
        # They seem to happen infrequently (on the order of once a day across
        # all jobs) so a retry is likely to work.
        container = retry_function(
            lambda: self.cloud.get_container(self.container))
        if not container:
            retry_function(
                lambda: self.cloud.create_container(
                    name=self.container, public=public))
            headers = {'X-Container-Meta-Web-Index': 'index.html',
                       'X-Container-Meta-Access-Control-Allow-Origin': '*'}
            retry_function(
                lambda: self.cloud.update_container(
                    name=self.container,
                    headers=headers))
            # 'X-Container-Meta-Web-Listings': 'true'

            # The ceph radosgw swift implementation requires an
            # index.html at the root in order for any other indexes to
            # work.
            index_headers = {'access-control-allow-origin': '*'}
            retry_function(
                lambda: self.cloud.create_object(self.container,
                                                 name='index.html',
                                                 data='',
                                                 content_type='text/html',
                                                 **index_headers))

            # Enable the CDN in rax
            if cdn_url:
                retry_function(lambda: self.cloud.session.put(cdn_url))

        if cdn_url:
            endpoint = retry_function(
                lambda: self.cloud.session.head(
                    cdn_url).headers['X-Cdn-Ssl-Uri'])
            container = endpoint
        else:
            endpoint = self.cloud.object_store.get_endpoint()
            container = os.path.join(endpoint, self.container)

        self.url = os.path.join(container, self.prefix)

    def upload(self, file_list):
        """Spin up thread pool to upload to swift"""

        if self.dry_run:
            return

        num_threads = min(len(file_list), MAX_UPLOAD_THREADS)
        threads = []
        # Keep track on upload failures
        failures = []

        queue = queuelib.Queue()
        # add items to queue
        for f in file_list:
            queue.put(f)

        for x in range(num_threads):
            t = threading.Thread(
                target=self.post_thread, args=(queue, failures)
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        return failures

    def post_thread(self, queue, failures):
        while True:
            try:
                file_detail = queue.get_nowait()
                logging.debug("%s: processing job %s",
                              threading.current_thread(),
                              file_detail)
                retry_function(lambda: self._post_file(file_detail))
            except requests.exceptions.RequestException as e:
                msg = "Error posting file after multiple attempts"
                # Do our best to attempt to upload all the files
                logging.exception(msg)
                failures.append({
                    "file": file_detail.filename,
                    "error": "{}: {}".format(msg, e)
                })
                continue
            except IOError as e:
                msg = "Error opening file"
                # Do our best to attempt to upload all the files
                logging.exception(msg)
                failures.append({
                    "file": file_detail.filename,
                    "error": "{}: {}".format(msg, e)
                })
                continue
            except queuelib.Empty:
                # No more work to do
                return

    @staticmethod
    def _is_text_type(mimetype):
        # We want to compress all text types.
        if mimetype.startswith('text/'):
            return True

        # Further compress types that typically contain text but are no
        # text sub type.
        compress_types = [
            'application/json',
            'image/svg+xml',
        ]
        if mimetype in compress_types:
            return True
        return False

    def _post_file(self, file_detail):
        relative_path = os.path.join(self.prefix, file_detail.relative_path)
        headers = {}
        if self.delete_after:
            headers['x-delete-after'] = str(self.delete_after)
        headers['content-type'] = file_detail.mimetype
        # This is required for Rackspace CDN
        headers['access-control-allow-origin'] = '*'

        if not file_detail.folder:
            if (file_detail.encoding is None and
                self._is_text_type(file_detail.mimetype)):
                headers['content-encoding'] = 'gzip'
                data = GzipFilter(open(file_detail.full_path, 'rb'))
            else:
                if (not file_detail.filename.endswith(".gz") and
                    file_detail.encoding):
                    # Don't apply gzip encoding to files that we receive as
                    # already gzipped. The reason for this is swift will
                    # serve this back to users as an uncompressed file if they
                    # don't set an accept-encoding that includes gzip. This
                    # can cause problems when the desired file state is
                    # compressed as with .tar.gz tarballs.
                    headers['content-encoding'] = file_detail.encoding
                data = open(file_detail.full_path, 'rb')
        else:
            data = ''
            relative_path = relative_path.rstrip('/')
            if relative_path == '':
                relative_path = '/'
        self.cloud.create_object(self.container,
                                 name=relative_path,
                                 data=data,
                                 **headers)


def run(cloud, container, files,
        indexes=True, parent_links=True, topdir_parent_link=False,
        partition=False, footer='index_footer.html', delete_after=15552000,
        prefix=None, public=True, dry_run=False):

    if prefix:
        prefix = prefix.lstrip('/')
    if partition and prefix:
        parts = prefix.split('/')
        if len(parts) > 1:
            container += '_' + parts[0]
            prefix = '/'.join(parts[1:])

    # Create the objects to make sure the arguments are sound.
    with FileList() as file_list:
        # Scan the files.
        for file_path in files:
            file_list.add(file_path)

        indexer = Indexer(file_list)

        # (Possibly) make indexes.
        if indexes:
            indexer.make_indexes(create_parent_links=parent_links,
                                 create_topdir_parent_link=topdir_parent_link,
                                 append_footer=footer)

        logging.debug("List of files prepared to upload:")
        for x in file_list:
            logging.debug(x)

        # Upload.
        uploader = Uploader(cloud, container, prefix, delete_after,
                            public, dry_run)
        upload_failures = uploader.upload(file_list)
        return uploader.url, upload_failures


def ansible_main():
    module = AnsibleModule(
        argument_spec=dict(
            cloud=dict(required=True, type='raw'),
            container=dict(required=True, type='str'),
            files=dict(required=True, type='list'),
            partition=dict(type='bool', default=False),
            indexes=dict(type='bool', default=True),
            parent_links=dict(type='bool', default=True),
            topdir_parent_link=dict(type='bool', default=False),
            public=dict(type='bool', default=True),
            footer=dict(type='str'),
            delete_after=dict(type='int'),
            prefix=dict(type='str'),
        )
    )

    p = module.params
    cloud = get_cloud(p.get('cloud'))
    try:
        url, upload_failures = run(
            cloud, p.get('container'), p.get('files'),
            indexes=p.get('indexes'),
            parent_links=p.get('parent_links'),
            topdir_parent_link=p.get('topdir_parent_link'),
            partition=p.get('partition'),
            footer=p.get('footer'),
            delete_after=p.get('delete_after', 15552000),
            prefix=p.get('prefix'),
            public=p.get('public')
        )
    except (keystoneauth1.exceptions.http.HttpError,
            requests.exceptions.RequestException):
        s = "Error uploading to %s.%s" % (cloud.name, cloud.config.region_name)
        logging.exception(s)
        s += "\n" + traceback.format_exc()
        module.fail_json(
            changed=False,
            msg=s,
            cloud=cloud.name,
            region_name=cloud.config.region_name)
    module.exit_json(
        changed=True,
        url=url,
        upload_failures=upload_failures,
    )


def cli_main():
    parser = argparse.ArgumentParser(
        description="Upload files to swift"
    )
    parser.add_argument('--verbose', action='store_true',
                        help='show debug information')
    parser.add_argument('--no-indexes', action='store_true',
                        help='do not generate any indexes at all')
    parser.add_argument('--no-parent-links', action='store_true',
                        help='do not include links back to a parent dir')
    parser.add_argument('--create-topdir-parent-link', action='store_true',
                        help='include a link in the root directory of the '
                             'files to the parent directory which may be the '
                             'index of all results')
    parser.add_argument('--no-public', action='store_true',
                        help='do not create the container as public')
    parser.add_argument('--partition', action='store_true',
                        help='partition the prefix into multiple containers')
    parser.add_argument('--append-footer', default='index_footer.html',
                        help='when generating an index, if the given file is '
                             'present in a directory, append it to the index '
                             '(set to "none" to disable)')
    parser.add_argument('--delete-after', default=15552000,
                        help='Number of seconds to delete object after '
                             'upload. Default is 6 months (15552000 seconds) '
                             'and if set to 0 X-Delete-After will not be set',
                        type=int)
    parser.add_argument('--prefix',
                        help='Prepend this path to the object names when '
                             'uploading')
    parser.add_argument('--dry-run', action='store_true',
                        help='do not attempt to create containers or upload, '
                             'useful with --verbose for debugging')
    parser.add_argument('cloud',
                        help='Name of the cloud to use when uploading')
    parser.add_argument('container',
                        help='Name of the container to use when uploading')
    parser.add_argument('files', nargs='+',
                        help='the file(s) to upload with recursive glob '
                        'matching when supplied as a string')

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
        # Set requests log level accordingly
        logging.getLogger("requests").setLevel(logging.DEBUG)
        # logging.getLogger("keystoneauth").setLevel(logging.INFO)
        # logging.getLogger("stevedore").setLevel(logging.INFO)
        logging.captureWarnings(True)

    append_footer = args.append_footer
    if append_footer.lower() == 'none':
        append_footer = None

    url, _ = run(
        get_cloud(args.cloud), args.container, args.files,
        indexes=not args.no_indexes,
        parent_links=not args.no_parent_links,
        topdir_parent_link=args.create_topdir_parent_link,
        partition=args.partition,
        footer=append_footer,
        delete_after=args.delete_after,
        prefix=args.prefix,
        public=not args.no_public,
        dry_run=args.dry_run
    )
    print(url)


if __name__ == '__main__':
    # Avoid unactionable warnings
    requestsexceptions.squelch_warnings(
        requestsexceptions.InsecureRequestWarning)

    # The zip/ansible/modules check is required for Ansible 5 because
    # stdin may be a tty, but does not work in ansible 2.8.  The tty
    # check works on versions 2.8, 2.9, and 6.
    if ('.zip/ansible/modules' in sys.argv[0] or not sys.stdin.isatty()):
        ansible_main()
    else:
        cli_main()
