# Copyright (c) 2016 Red Hat
#
# This module is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software.  If not, see <http://www.gnu.org/licenses/>.
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os
import subprocess
import tempfile

from ansible.module_utils.basic import AnsibleModule


def afs_sync(afssource, afstarget):
    # Find the list of root markers in the just-completed build
    # (usually there will only be one, but some builds produce content
    # at the root *and* at a tag location, or possibly at multiple
    # translation roots).
    src_root_markers = []
    for root, dirnames, filenames in os.walk(afssource):
        if '.root-marker' in filenames:
            src_root_markers.append(root)

    output_blocks = []
    # Synchronize the content at each root marker.
    for root_count, src_root in enumerate(src_root_markers):
        # The component of the path between the source root and the
        # current source root marker.  May be '.' if there is a marker
        # at the root.
        subpath = os.path.relpath(src_root, afssource)

        # Add to our debugging output
        output = dict(subpath=subpath)
        output_blocks.append(output)

        # The absolute path to the source (in staging) and destination
        # (in afs) of the build root for the current root marker.
        subsource = os.path.abspath(os.path.join(afssource, subpath))
        subtarget = os.path.abspath(os.path.join(afstarget, subpath))

        # Create a filter list for rsync so that we copy exactly the
        # directories we want to without deleting any existing
        # directories in the published site that were placed there by
        # previous builds.

        # Exclude any directories under this subpath which have root
        # markers.
        excludes = []
        for root, dirnames, filenames in os.walk(subtarget):
            if '.root-marker' in filenames:
                exclude_subpath = os.path.relpath(root, subtarget)
                if exclude_subpath == '.':
                    continue
                excludes.append(os.path.join('/', exclude_subpath))
        output['excludes'] = excludes

        filter_file = tempfile.NamedTemporaryFile(delete=False)

        for exclude in excludes:
            filter_file.write(b'- %s\n' % exclude.encode('utf8'))
        filter_file.close()

        # Perform the rsync with the filter list.
        rsync_cmd = ' '.join([
            '/usr/bin/rsync', '-rtp', '--safe-links', '--delete-after',
            "--out-format='<<CHANGED>>%i %n%L'",
            "--filter='merge {filter}'", '{src}/', '{dst}/',
        ])
        mkdir_cmd = ' '.join(['mkdir', '-p', '{dst}/'])
        bash_cmd = ' '.join([
            '/bin/bash', '-c', '"{mkdir_cmd} && {rsync_cmd}"'
        ]).format(
            mkdir_cmd=mkdir_cmd,
            rsync_cmd=rsync_cmd)

        shell_cmd = bash_cmd.format(
            src=subsource,
            dst=subtarget,
            filter=filter_file.name)
        output['source'] = subsource
        output['destination'] = subtarget
        output['output'] = subprocess.check_output(shell_cmd, shell=True)
        os.remove(filter_file.name)

    return output_blocks


def main():
    module = AnsibleModule(
        argument_spec=dict(
            source=dict(required=True, type='raw'),
            target=dict(required=True, type='raw'),
        )
    )

    p = module.params
    output = afs_sync(p['source'], p['target'])
    module.exit_json(changed=True, build_roots=output)


if __name__ == '__main__':
    main()
