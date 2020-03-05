"""
Hacky upgrade script for 2020-03-05... we should instead use the tools in
the-quest-entities on the next upgrade
"""
import json
import click
import tarfile
import os
from collections import OrderedDict
from datetime import datetime

DATETIME_FORMAT = '%Y-%m-%dT%H:%M:%S'
FUNDING_AMOUNT = 100_000_000_000_000


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
@click.option('--genesis-dump', default="", required=False,
              type=click.Path(exists=True, resolve_path=True))
@click.option('--genesis-dump-height', default=0, required=False,
              type=click.INT)
@click.option('--genesis-save-path', default="", required=False,
              type=click.Path(resolve_path=True))
@click.option('--chain-id-prefix', default='questnet')
@click.option('--genesis-time',
              default=datetime.now().strftime(DATETIME_FORMAT),
              help='Date time of deployment in UTC as iso8601',
              type=click.DateTime(formats=[DATETIME_FORMAT]))
@click.option('--new-halt-epoch', default=6525, required=True,
              type=click.INT)
@click.option('--dry-run-entities-path', required=False,
              type=click.Path(resolve_path=True))
@click.option('--dry-run/--no-dry-run', default=False)
def upgrade(genesis_dump, genesis_dump_height, genesis_save_path,
            chain_id_prefix, genesis_time, new_halt_epoch,
            dry_run_entities_path, dry_run):
    if genesis_dump == "":
        # Somehow download the genesis dump from the running node
        raise Exception('Not yet implemented')
    genesis_dict = json.load(
        open(genesis_dump), object_pairs_hook=OrderedDict)

    genesis_dict['staking']['params']['thresholds'] = {
        '0': '100000000000',
        '1': '100000000000',
        '2': '100000000000',
        '3': '100000000000',
        '4': '100000000000',
        '5': '100000000000',
        '6': '100000000000',
    }

    genesis_dict['staking']['params']['commission_schedule_rules'] = {
        'rate_change_interval': 1,
        'rate_bound_lead': 14,
        'max_rate_steps': 21,
        'max_bound_steps': 21,
    }

    del genesis_dict['staking']['params']['disable_transfers']
    del genesis_dict['staking']['params']['disable_delegation']
    del genesis_dict['staking']['params']['undisable_transfers_from']
    del genesis_dict['staking']['params']['fee_weight_vote']

    genesis_dict['staking']['params']['fee_split_vote'] = '1'
    genesis_dict['staking']['params']['fee_split_propose'] = '1'

    chain_id_date_and_timestamp = genesis_time.strftime('%Y-%m-%d-%s')
    genesis_dict['chain_id'] = '%s-%s' % (chain_id_prefix,
                                          chain_id_date_and_timestamp)

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

        new_total_supply = total_supply + FUNDING_AMOUNT * len(test_nodes)

        # Apologies
        # This is super hacky we should make sure to use the tools already
        # written in the-quest-entities in the future. The intent is that we
        # should have ways to reliably edit the staking application for testing
        # genesis dumps from the currently running network.
        genesis_dict['staking']['total_supply'] = new_total_supply
        for entity_id in added_entity_ids:
            genesis_dict['staking']['ledger'][entity_id] = {
                'general': {
                    'balance': '0',
                    'nonce': 0
                },
                'escrow': {
                    'active': {
                        'balance': '%d' % FUNDING_AMOUNT,
                        'total_shares': '%d' % FUNDING_AMOUNT
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
                    'shares': '%d' % FUNDING_AMOUNT
                }
            }

        genesis_dict['staking']['total_supply'] = '%d' % new_total_supply

    genesis_dict['halt_epoch'] = new_halt_epoch

    if genesis_save_path:
        json.dump(genesis_dict, open(genesis_save_path, 'w'), indent=2)


if __name__ == "__main__":
    upgrade()
