import asyncio
import json
import os
import re
from string import Formatter

import aiofiles
import toml


async def launch_module(module_name, target):
    module_class = getattr(__import__(f'modules.{module_name}',
                                      fromlist=[module_name]), module_name)
    return module_class(target)


def prepare_conf_string(string, storage):
    return string.format(**storage)


def get_fields(string):
    """
    If keywords are dict access, we only look for dict name
    Example: "My command needs {credentials[password]}"
    We will return "credentials" only
    """
    return [i[1].split('[')[0] for i in Formatter().parse(string) if i[1]]


async def run_cmd(event_action, target, event_manager, logger, storage):
    cmd = event_action['cmd']
    logger.info(f"Starting {event_action['name']}")
    logger.debug(f"{event_action['name']} cmd: {cmd}")
    process = await asyncio.create_subprocess_shell(cmd,
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT,
                                                    executable='/bin/bash')
    # Add all static variables configured
    if 'store_static' in event_action:
        for storage_info in event_action['store_static']:
            for key, to_store in storage_info.items():
                logger.debug(f"{event_action['name']} store static: {key}:{to_store}")
                to_store = prepare_conf_string(to_store, storage)
                if target.stored.get(key, None) != to_store:
                    async with target.lock:
                        await event_manager.store(key, to_store)
    while True:
        line = await process.stdout.readline()
        if line:
            line = str(line.rstrip(), 'utf8', 'ignore')
            logger.debug(line)
            # Check if a regex matches meaning we have to store something
            if 'store' in event_action:
                for storage_info in event_action['store']:
                    for key, regex in storage_info.items():
                        match = re.findall(regex, line)
                        if match:
                            async with target.lock:
                                await event_manager.store(key, match[0])
            if 'append_array' in event_action:
                for array_info in event_action['append_array']:
                    for key, regex in array_info.items():
                        match = re.findall(regex, line)
                        if match:
                            async with target.lock:
                                await event_manager.append(key, match[0])
            if 'append_dict_array' in event_action:
                for array_info in event_action['append_dict_array']:

                    for info_key, info_values in array_info.items():
                        dict_to_build = {}
                        for key, regex in info_values.items():
                            match = re.findall(regex, line)
                            if match:
                                dict_to_build[key] = match[0]
                        if dict_to_build != {}:
                            async with target.lock:
                                await event_manager.append(info_key, dict_to_build)
            # Check if a pattern launching a new event is detected
            if 'events' in event_action:
                for patterns in event_action['events']:
                    for pattern, events in patterns.items():
                        matches = re.findall(pattern, line)
                        if matches:
                            for event in events:
                                await event_manager.new_event(event)
        else:
            break
    await process.wait()
    logger.info(f"Ending {event_action['name']}")
    async with aiofiles.open(os.path.join(target.stored['output_dir'], 'stored_values.txt'), mode='w') as f:
        await f.write(json.dumps(event_manager.target.stored))
    logger.nb_tasks = len([t for t in event_manager.tasks if not t.cancelled() and not t.done()])


async def listen(listener, target, event_manager, logger, storage):
    logger.info(f"Starting {listener['name']}")
    # Add all static variables configured
    if 'store_static' in listener.keys():
        for dictionary in listener['store_static']:
            for key, to_store in dictionary.items():
                to_store = prepare_conf_string(to_store, storage)
                if target.stored.get(key, None) != to_store:
                    async with target.lock:
                        await event_manager.store(key, to_store)
    async with aiofiles.open(listener['file']) as f:
        while True:
            line = await f.readline()
            if line:
                logger.debug(line)
                # Check if a regex matches meaning we have to store something
                if 'store' in listener.keys():
                    for dictionary in listener['store']:
                        for key, regex in dictionary.items():
                            match = re.findall(regex, line)
                            if match:
                                async with target.lock:
                                    await event_manager.store(key, match[0])
                if 'append_array' in listener.keys():
                    for dictionary in listener['append_array']:
                        for key, regex in dictionary.items():
                            match = re.findall(regex, line)
                            if match:
                                async with target.lock:
                                    await event_manager.append(key, match[0])
                # Check if a pattern launching a new event is detected
                if 'events' in listener.keys():
                    logger.debug('events')
                    for dictionary in listener['events']:
                        for pattern, events in dictionary.items():
                            matches = re.findall(pattern, line)
                            if matches:
                                for event in events:
                                    await event_manager.new_event(event)
            else:
                await asyncio.sleep(5)


def generate_graph(workflow):
    from graphviz import Digraph
    dot = Digraph(comment='Workflow', format='png')
    workflow = toml.load(os.path.join(os.path.dirname(__file__), f"../conf/{workflow}.toml"))
    edges = set()
    for event, commands in workflow.items():
        label = f"""<<TABLE BORDER="0">
        <TR><TD><B>{event}</B></TD></TR>
"""
        for command in commands:
            label += f"    <TR><TD>{command['name']}</TD></TR>"
            for pattern in command.get('patterns', []):
                for events_to_fire in pattern.values():
                    for event_to_fire in events_to_fire:
                        edges.add((event, event_to_fire))
        label += "</TABLE>>"
        dot.node(event, label=label)
    for edge in edges:
        dot.edge(edge[0], edge[1])
    dot.render('output/workflow.gv', view=True)


def parse_url(url):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    domain = parsed.hostname
    top_domain = '.'.join(domain.split('.')[-2:]) if domain.count('.') > 1 else domain
    base_url = f'{parsed.scheme}://{parsed.netloc}{parsed.path}'
    if parsed.path == '':
        base_url = f'{base_url}/'
    return domain, top_domain, base_url
