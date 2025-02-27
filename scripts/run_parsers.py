#!/usr/bin/env python3

from typing import Tuple
import git
import glob
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from packaging import version
from datetime import datetime

class Groundskeeper:
    repository_url = 'https://github.com/ArduPilot/ardupilot'
    repository_name = repository_url.split('/')[-1]
    tag_regex = re.compile(r'(?P<name>[A-z]+(\d+)?)(-(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)?|-(?P<beta>beta))$')
    temp_folder = tempfile.mkdtemp()

    valid_name_map = {
        'APMrover2': 'Rover',
        'ArduCopter': 'Copter',
        'ArduPlane': 'Plane',
        'ArduSub': 'Sub',
        'Copter': 'Copter',
        'Plane': 'Plane',
        'Rover': 'Rover',
    }

    def __init__(self):
        self.repository_path = os.path.join(self.temp_folder, self.repository_name)
        print(f"""Using:
    Temporary folder: {self.temp_folder}
    Repository: {self.repository_url}
""")

    def get_version_for_tag(self, tag) -> Tuple[int, int]:
      if tag['matches']['major'] is not None:
          return int(tag['matches']['major']), int(tag['matches']['minor'])
      else:
          # fetch number from version.h in the vehicle folder in the given tag
          self.repository.git.checkout(tag["tag"], force=True)
          # try to read <vehicle>/version.h
          path1 = Path(f'{self.repository_path}/{tag["matches"]["name"]}/version.h')
          path2 = Path(f'{self.repository_path}/{self.valid_name_map[tag["matches"]["name"]]}/version.h')
          file = path1 if path1.exists() else path2
          with open(file=file, mode='r') as version_file:
              content = version_file.read()
              match = re.search(r'#define\s+FW_MAJOR\s+(\d+)', content)
              major = int(match.group(1))
              match = re.search(r'#define\s+FW_MINOR\s+(\d+)', content)
              minor = int(match.group(1))
              return major, minor

    def clone_repository(self):
        print(f'Starting cloning to: {self.repository_path}')
        #self.repository_path = '/tmp/tmpetcgh7ni/ardupilot'
        #return git.Repo(self.repository_path)
        return git.Repo.clone_from(self.repository_url, self.repository_path)

    def get_last_ground_change(repository: git.Repo):
        last_commit_date = repository.head.commit.committed_date
        return datetime.fromtimestamp(last_commit_date)


    def run(self):
        self.repository = self.clone_repository()
        tag_names = [tag.path[len('refs/tags/'):] for tag in self.repository.tags]
        last_ground_change = Groundskeeper.get_last_ground_change(git.Repo(Path(__file__).parent.parent))

        # Prepare for MAVLink parsing - always use the latest script (since it might cover new messages)
        #shutil.copy(f'{self.repository_path}/Tools/scripts/mavlink_parse.py', self.temp_folder)
        # TEMPORARY WORKAROUND UNTIL ArduPilot/ardupilot#27226 IS MERGED
        from urllib.request import urlretrieve
        urlretrieve("https://raw.githubusercontent.com/ES-Alexander/ardupilot/refs/heads/mavlink-messages-rst/Tools/scripts/mavlink_parse.py", f'{self.temp_folder}/mavlink_parse.py')
        # TEMP-END

        # Get only valid tag names
        tags = [
            {
                'tag': self.tag_regex.search(tag)[0],
                'reference': tag,
                'matches': {**self.tag_regex.search(tag).groupdict()}
            } for tag in tag_names if self.tag_regex.search(tag)
        ]
        tags = [tag for tag in tags if self.valid_name_map.get(tag['matches']['name'])]
        # Get only the newest patch version
        old_versions = []
        previous_tag = None
        for tag in tags:
            # Beta releases are unique, we don't need to compare them
            if tag['matches']['beta']:
                continue
            if previous_tag:
                print(f'{previous_tag["tag"]} => {tag["tag"]}')
            if not previous_tag or (
                    tag['matches']['name'] != previous_tag['matches']['name']
                    or tag['matches']['major'] != previous_tag['matches']['major']
                    or tag['matches']['minor'] != previous_tag['matches']['minor']
                ):
                previous_tag = tag
                continue
            if tag['matches']['patch'] > previous_tag['matches']['patch']:
                print(f'Remove {previous_tag["tag"]}')
                old_versions.append(previous_tag['tag'])
                previous_tag = tag
            else:
                print(f'Remove {tag["tag"]}')
                old_versions.append(tag['tag'])

        tags = [tag for tag in tags if tag['tag'] not in old_versions]

        # Generate parameters for all tags
        for tag in tags:
            tag_name = tag['tag']
            tag_simple_name = tag['matches']['name']
            tag_major_version, tag_minor_version = self.get_version_for_tag(tag)
            vehicle_type = self.valid_name_map.get(tag_simple_name)
            tag_reference = tag['reference']
            folder_name = f'{vehicle_type}-{tag_major_version}.{tag_minor_version}'

            if not vehicle_type:
                continue

            print(f'Processing: {folder_name}..')
            # Old versions are not mantained and generation of the files is not fully supported
            if tag_major_version < 4:
                print("Ignoring old version")
                continue

            # Ignore local changes (they're automated anyway)
            self.repository.git.checkout(tag_reference, force=True)
            tag_date = Groundskeeper.get_last_ground_change(self.repository)
            if last_ground_change > tag_date:
                print(f"Version already generated for {tag_date}")
                continue

            # Run parameters parser
            try:
                subprocess.run([f'{self.repository_path}/Tools/autotest/param_metadata/param_parse.py', '--vehicle', vehicle_type], cwd=self.repository_path)
            except Exception as exception:
                print(exception)

            output = Path(__file__).parent.parent
            subprocess.run(['mkdir', '-p', output])
            dest = f"{output}/{folder_name}"
            subprocess.run(['mkdir', '-p', dest])
            for data in glob.glob(f'{dest}/*'):
                    os.remove(data)
            for data in glob.glob(f'{self.repository_path}/Parameter*'):
                shutil.copy2(data, dest)
                os.remove(data)
            for data in glob.glob(f'{self.repository_path}/apm.pdef.*'):
                shutil.copy2(data, dest)
                os.remove(data)

            # Run MAVLink messages parser
            vehicle = f'{"Ardu" if vehicle_type != "Rover" else ""}{vehicle_type}'
            try:
                # The parser expects to be run from its normal place in the repository
                script_folder = f'{self.repository_path}/Tools/scripts/'
                script_file = script_folder + 'mavlink_parse.py'
                shutil.copy(f'{self.temp_folder}/mavlink_parse.py', script_file)
                subprocess.run(['python', script_file, '-cguq', '--header', 'ardupilot_wiki', '--format', 'rst',
                                '--filename', f'{dest}/MAVLinkMessages.rst', '--branch', folder_name, '--vehicle', vehicle],
                               cwd=script_folder)
            except Exception as exception:
                print(exception)


G = Groundskeeper()
G.run()
