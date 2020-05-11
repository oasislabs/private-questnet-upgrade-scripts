"""
Hacky upgrade script for 2020-03-05... we should instead use the tools in
the-quest-entities on the next upgrade
"""
import json
import tarfile
import os
import sys
import subprocess
import shutil
import io
import time
import slack
import click
import requests
import base64
from collections import OrderedDict
from datetime import datetime
import cbor

DATETIME_FORMAT = '%Y-%m-%dT%H:%M:%S'
FUNDING_AMOUNT = 100_000_000_000_000
OASIS_NODE_URL = 'https://github.com/oasislabs/oasis-core/releases/download/v{version}/oasis-node_{version}_linux_amd64.tar.gz'
OASIS_NODE_DOWNLOAD_PATH = '/tmp'


# this list is referenced here: https://docs.google.com/document/d/19xDr6nP1PQTOrgPGz38NvNDKxbjvE2qMo154Xbwxmq4/edit#
ENTITY_IDS_TO_REMOVE = [
    'TuY4U427wGNzfptNpTWZSr6cG63Uj+4Yht8Vihc5wB4=',
    'XfJLjTeXXEqGY96qc8yfwqmixtVO0m1HcDt0PsZt6o0=',
    'ZAI04nVzFQGQQX9s7nHo06x1EPBBvflUeJSdrhIU+VE=',
    'dEM3CsbXRYqk8xIGiCu3tm97Y0oe+wqTq+8MS2CDjL8=',
    'oOuR+C5hFOszGIzdo/BuT5yVkDLrfnQs5Lx2BYxW4eg=',
    'o2z90JLrbqGTyNqFV38xW0GauSNSCAtYcV8Ky82Lx7s=',
    '04WML0Y0uUS4eXpHqV9tdYzgJzORT0L/8XXNvnytZQY=',
    '9cHQftDsKcdK7mMajHQ8u/NQ0s9I1H27b45Cfam24KY=',
]


class OasisNodeBinary(object):
    """Abstracts calling a specific version of the oasis-node binary"""
    @classmethod
    def version(cls, version):
        download_url = OASIS_NODE_URL.format(version=version)
        oasis_node_tarball_path = os.path.join(
            OASIS_NODE_DOWNLOAD_PATH, 'oasis-node-%s.tar.gz' % version)
        oasis_node_path = os.path.join(
            OASIS_NODE_DOWNLOAD_PATH, 'oasis-node-%s' % version)
        with requests.get(download_url, stream=True) as r:
            with open(oasis_node_tarball_path, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        subprocess.call(['tar', 'xvf', oasis_node_tarball_path],
                        cwd=OASIS_NODE_DOWNLOAD_PATH)
        shutil.move(
            os.path.join(OASIS_NODE_DOWNLOAD_PATH, 'oasis-node'),
            oasis_node_path
        )
        os.chmod(oasis_node_path, 0o755)
        return cls(oasis_node_path)

    def __init__(self, oasis_node_path):
        self._path = oasis_node_path

    def call(self, *args):
        command = [self._path]
        command.extend(args)
        return subprocess.check_call(command)


class Entity(object):
    @classmethod
    def load_package(cls, path):
        package = tarfile.open(path, 'r:*')
        node_genesis = json.load(package.extractfile(
            package.getmember('node/node_genesis.json')))
        entity_genesis = json.load(package.extractfile(
            package.getmember('entity/entity_genesis.json')))
        return cls(entity_genesis, node_genesis)

    def __init__(self, entity_genesis, node_genesis):
        self._entity_genesis = entity_genesis
        self._node_genesis = node_genesis

    @property
    def node_genesis(self):
        return self._node_genesis

    @property
    def entity_genesis(self):
        return self._entity_genesis


def load_entities_dir(entity_dir_path):
    entities = []
    return entities


@click.command()
@click.option('--genesis-dump', default="", required=False)
@click.option('--genesis-dump-height', default=591600, required=False,
              type=click.INT)
@click.option('--genesis-save-path', default="", required=False)
@click.option('--chain-id-prefix', default='questnet')
@click.option('--genesis-time',
              default=datetime.now().strftime(DATETIME_FORMAT),
              help='Date time of deployment in UTC as iso8601',
              type=click.DateTime(formats=[DATETIME_FORMAT]))
@click.option('--new-halt-epoch', default=11000, required=True,
              type=click.INT)
@click.option('--dry-run-entities-path', required=False,
              type=click.Path(resolve_path=True))
@click.option('--dry-run/--no-dry-run', default=False)
@click.option('--current-version', default='20.4.1')
@click.option('--client-address',
              default=os.environ.get('OASIS_CLIENT_SERVICE_PORT', ''))
@click.option('--slack-api-token',
              default=os.environ.get('SLACK_API_TOKEN', ''))
@click.option('--slack-channel', default='#mainnet-dryrun')
def upgrade(genesis_dump, genesis_dump_height, genesis_save_path,
            chain_id_prefix, genesis_time, new_halt_epoch,
            dry_run_entities_path, dry_run, current_version,
            client_address, slack_api_token, slack_channel):
    if genesis_dump == "":
        if not client_address:
            raise Exception('no configured address to call a questnet node')

        # Load the current questnet version that is running
        oasis_node_current = OasisNodeBinary.version(current_version)

        genesis_dump = '/tmp/genesis.json'

        # Download the dump
        oasis_node_current.call(
            'genesis', 'dump',
            '--height', '%d' % genesis_dump_height,
            '-a', client_address,
            '--genesis.file', genesis_dump
        )
    else:
        genesis_dump = os.path.abspath(os.path.expanduser(genesis_dump))

    # Parse Genesis Document
    genesis_dict = json.load(
        open(genesis_dump), object_pairs_hook=OrderedDict)

    # Remove registry entities just filter everyone out who matches
    updated_entities = []
    existing_entities = genesis_dict['registry']['entities']
    nodes_to_delete = []
    for existing_entity in existing_entities:
        entity_id = existing_entity['signature']['public_key']
        escrow_amount = get_entity_escrow_amount(genesis_dict, entity_id)
        if escrow_amount < int(genesis_dict['staking']['params']['thresholds']['0']):
            entity_descriptor = cbor.loads(base64.b64decode(
                existing_entity['untrusted_raw_value']))

            for raw_node_id in entity_descriptor['nodes']:
                nodes_to_delete.append(
                    base64.b64encode(raw_node_id).decode('utf-8'))
            print("removing %s" % entity_id)
            continue
        updated_entities.append(existing_entity)
    genesis_dict['registry']['entities'] = updated_entities

    updated_nodes = []
    for existing_node in genesis_dict['registry']['nodes']:
        signatures = set(
            map(lambda a: a['public_key'], existing_node['signatures']))
        node_ids = signatures.intersection(set(nodes_to_delete))
        if len(node_ids) > 0:
            print("removing a nodes %s" % node_ids)
            continue
        updated_nodes.append(existing_node)
    genesis_dict['registry']['nodes'] = updated_nodes

    for node_to_delete in nodes_to_delete:
        if node_to_delete in genesis_dict['registry']['node_statuses']:
            del genesis_dict['registry']['node_statuses'][node_to_delete]

    # # Remove unnecessary staking params
    # del genesis_dict['staking']['params']['fee_split_vote']
    # del genesis_dict['staking']['params']['fee_split_propose']

    # # Add new staking params
    # genesis_dict['staking']['params']['fee_split_weight_vote'] = '1'
    # genesis_dict['staking']['params']['fee_split_weight_propose'] = '2'
    # genesis_dict['staking']['params']['fee_split_weight_next_propose'] = '1'

    # Rename max_evidence_age
    max_evidence_age_blocks = genesis_dict['consensus']['params']['max_evidence_age']
    del genesis_dict['consensus']['params']['max_evidence_age']
    genesis_dict['consensus']['params']['max_evidence_age_blocks'] = max_evidence_age_blocks

    # Add max_evidence_age_time
    genesis_dict['consensus']['params']['max_evidence_age_time'] = 172800000000000

    # Update chain id
    chain_id_date_and_timestamp = genesis_time.strftime('%Y-%m-%d-%s')
    genesis_dict['chain_id'] = '%s-%s' % (chain_id_prefix,
                                          chain_id_date_and_timestamp)

    genesis_dict['genesis_time'] = genesis_time.strftime(
        '%Y-%m-%dT%H:%M:%S.000000000Z')

    # Generate dry run genesis document
    if dry_run:
        genesis_dict['epochtime']['base'] = 0

        # Add test entities
        genesis_entities = genesis_dict['registry']['entities']

        added_entity_ids = []

        test_nodes = []
        for filename in os.listdir(dry_run_entities_path):
            if filename.endswith('.tar.gz'):
                entity = Entity.load_package(
                    os.path.join(dry_run_entities_path, filename))

                genesis_entities.append(entity.entity_genesis)
                added_entity_ids.append(
                    entity.entity_genesis['signature']['public_key'])
                test_nodes.append(entity.node_genesis)

        # Add all test nodes
        genesis_dict['registry']['nodes'] = test_nodes

        # Update total supply to add entities to staking application
        total_supply = int(genesis_dict['staking']['total_supply'])

        new_total_supply = total_supply + \
            FUNDING_AMOUNT * (len(test_nodes) + 1)

        # Apologies
        # This is super hacky we should make sure to use the tools already
        # written in the-quest-entities in the future. The intent is that we
        # should have ways to reliably edit the staking application for testing
        # genesis dumps from the currently running network.
        genesis_dict['staking']['total_supply'] = new_total_supply
        count = 0
        for entity_id in added_entity_ids:
            funding_amount = FUNDING_AMOUNT
            if count == 0:
                funding_amount = FUNDING_AMOUNT * 2
            count += 1
            genesis_dict['staking']['ledger'][entity_id] = {
                'general': {
                    'balance': '0',
                    'nonce': 0
                },
                'escrow': {
                    'active': {
                        'balance': '%d' % funding_amount,
                        'total_shares': '%d' % funding_amount
                    },
                    'debonding': {
                        'balance': '0',
                        'total_shares': '0'
                    },
                    'commission_schedule': {
                        'rates': None,
                        'bounds': None,
                    }
                }
            }
            genesis_dict['staking']['delegations'][entity_id] = {
                entity_id: {
                    'shares': '%d' % funding_amount
                }
            }

        genesis_dict['staking']['total_supply'] = '%d' % new_total_supply
        genesis_dict['scheduler']['params']['min_validators'] = len(
            added_entity_ids)

    genesis_dict['halt_epoch'] = new_halt_epoch

    genesis_json_str = json.dumps(genesis_dict, indent=2)

    if genesis_save_path:
        genesis_save_path = os.path.abspath(
            os.path.expanduser(genesis_save_path))
        with open(genesis_save_path, 'w') as genesis_save:
            genesis_save.write(genesis_json_str)

    if slack_api_token:
        print("slacking the team")
        # Upload file to slack and notify Peter G to not wake Reuven because
        # it's kinda funny
        client = slack.WebClient(token=slack_api_token)

        file_content = io.BytesIO()
        file_content.write(genesis_json_str.encode('utf-8'))
        file_content.seek(0)

        print("uploading files")

        client.files_upload(
            channels=slack_channel,
            filename='genesis.json',
            file=file_content,
            title='Automated Genesis Patch'
        )

        client.chat_postMessage(
            channel=slack_channel,
            text='If the genesis looks fine feel free to publish the genesis early',
        )


def get_entity_escrow_amount(genesis_dict, entity_id):
    try:
        escrow_amount = int(
            genesis_dict['staking']['ledger'][entity_id]['escrow']['active']['balance'])
        return escrow_amount
    except ValueError:
        return 0
    except KeyError:
        return 0


if __name__ == "__main__":
    upgrade()
