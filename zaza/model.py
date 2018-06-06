import asyncio
from async_generator import async_generator, yield_, asynccontextmanager
import logging
import os
import subprocess
import tempfile
import yaml
from oslo_config import cfg

from juju import loop
from juju.errors import JujuError
from juju.model import Model

from zaza import sync_wrapper


async def deployed(filter=None):
    # Create a Model instance. We need to connect our Model to a Juju api
    # server before we can use it.
    model = Model()
    # Connect to the currently active Juju model
    await model.connect_current()
    try:
        # list currently deploeyd services
        return list(model.applications.keys())
    finally:
        # Disconnect from the api server and cleanup.
        await model.disconnect()


def get_unit_from_name(unit_name, model):
    """Return the units that corresponds to the name in the given model

    :param unit_name: Name of unit to match
    :type unit_name: str
    :param model: Model to perform lookup in
    :type model: juju.model.Model
    :returns: Unit matching given name
    :rtype: juju.unit.Unit or None
    """
    app = unit_name.split('/')[0]
    unit = None
    for u in model.applications[app].units:
        if u.entity_id == unit_name:
            unit = u
            break
    else:
        raise Exception
    return unit


@asynccontextmanager
@async_generator
async def run_in_model(model_name):
    """Context manager for executing code inside a libjuju model
       Example of using run_in_model:
           async with run_in_model(model_name) as model:
               model.do_something()

    :param model_name: Name of model to run function in
    :type model_name: str
    :returns: The juju Model object correcsponding to model_name
    :rtype: Iterator[:class:'juju.Model()']
    """
    model = Model()
    await model.connect_model(model_name)
    await yield_(model)
    await model.disconnect()


async def async_scp_to_unit(model_name, unit_name, source, destination,
                            user='ubuntu', proxy=False, scp_opts=''):
    """Transfer files to unit_name in model_name.

    :param model_name: Name of model unit is in
    :type model_name: str
    :param unit_name: Name of unit to scp to
    :type unit_name: str
    :param source: Local path of file(s) to transfer
    :type source: str
    :param destination: Remote destination of transferred files
    :type source: str
    :param user: Remote username
    :type source: str
    :param proxy: Proxy through the Juju API server
    :type proxy: bool
    :param scp_opts: Additional options to the scp command
    :type scp_opts: str
    """
    async with run_in_model(model_name) as model:
        unit = get_unit_from_name(unit_name, model)
        await unit.scp_to(source, destination, user=user, proxy=proxy,
                          scp_opts=scp_opts)

scp_to_unit = sync_wrapper(async_scp_to_unit)


async def async_scp_to_all_units(model_name, application_name, source,
                                 destination, user='ubuntu', proxy=False,
                                 scp_opts=''):
    """Transfer files from to all units of an application

    :param model_name: Name of model unit is in
    :type model_name: str
    :param application_name: Name of application to scp file to
    :type application_name: str
    :param source: Local path of file(s) to transfer
    :type source: str
    :param destination: Remote destination of transferred files
    :type source: str
    :param user: Remote username
    :type source: str
    :param proxy: Proxy through the Juju API server
    :type proxy: bool
    :param scp_opts: Additional options to the scp command
    :type scp_opts: str
    """
    async with run_in_model(model_name) as model:
        for unit in model.applications[application_name].units:
            # FIXME: Should scp in parallel
            await unit.scp_to(source, destination, user=user, proxy=proxy,
                              scp_opts=scp_opts)

scp_to_all_units = sync_wrapper(async_scp_to_all_units)


async def async_scp_from_unit(model_name, unit_name, source, destination,
                              user='ubuntu', proxy=False, scp_opts=''):
    """Transfer files from to unit_name in model_name.

    :param model_name: Name of model unit is in
    :type model_name: str
    :param unit_name: Name of unit to scp from
    :type unit_name: str
    :param source: Remote path of file(s) to transfer
    :type source: str
    :param destination: Local destination of transferred files
    :type source: str
    :param user: Remote username
    :type source: str
    :param proxy: Proxy through the Juju API server
    :type proxy: bool
    :param scp_opts: Additional options to the scp command
    :type scp_opts: str
    """
    async with run_in_model(model_name) as model:
        unit = get_unit_from_name(unit_name, model)
        await unit.scp_from(source, destination, user=user, proxy=proxy,
                            scp_opts=scp_opts)


scp_from_unit = sync_wrapper(async_scp_from_unit)


async def async_run_on_unit(model_name, unit_name, command, timeout=None):
    """Juju run on unit

    :param model_name: Name of model unit is in
    :type model_name: str
    :param unit_name: Name of unit to match
    :type unit: str
    :param command: Command to execute
    :type command: str
    :param timeout: How long in seconds to wait for command to complete
    :type timeout: int
    :returns: action.data['results'] {'Code': '', 'Stderr': '', 'Stdout': ''}
    :rtype: dict
    """
    async with run_in_model(model_name) as model:
        unit = get_unit_from_name(unit_name, model)
        action = await unit.run(command, timeout=timeout)
        if action.data.get('results'):
            return action.data.get('results')
        else:
            return {}

run_on_unit = sync_wrapper(async_run_on_unit)


async def async_get_unit_time(model_name, unit_name, timeout=None):
    """ Get the current time (in seconds since Epoch) on the given unit

    :param model_name: Name of model to query.
    :type model_name: str
    :param unit_name: Name of unit to run action on
    :type unit_name: str
    :returns: time in seconds since Epoch on unit
    :rtype: int
    """
    out = await async_run_on_unit(
        model_name,
        unit_name,
        "date +'%s'",
        timeout=timeout)
    return int(out['Stdout'])

get_unit_time = sync_wrapper(async_get_unit_time)


async def async_get_unit_service_start_time(model_name, unit_name, service,
                                            timeout=None):
    """Return the time (in seconds since Epoch) that the given service was
       started on the given unit. If the service is not running return 0

    :param model_name: Name of model to query.
    :type model_name: str
    :param unit_name: Name of unit to run action on
    :type unit_name: str
    :param service: Name of service to check is running
    :type service: str
    :param timeout: Time to wait for status to be achieved
    :type timeout: int
    :returns: time in seconds since Epoch on unit
    :rtype: int
    :raises: ServiceNotRunning
    """
    cmd = "stat -c %Y /proc/$(pidof -x {} | cut -f1 -d ' ')".format(service)
    out = await async_run_on_unit(model_name, unit_name, cmd, timeout=timeout)
    out = out['Stdout'].strip()
    if out:
        return int(out)
    else:
        raise ServiceNotRunning(service)

get_unit_service_start_time = sync_wrapper(async_get_unit_service_start_time)


async def async_get_application(model_name, application_name):
    """Return an application object

    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application to retrieve units for
    :type application_name: str

    :returns: Appliction object
    :rtype: object
    """
    async with run_in_model(model_name) as model:
        return model.applications[application_name]

get_application = sync_wrapper(async_get_application)


async def async_get_units(model_name, application_name):
    """Return all the units of a given application

    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application to retrieve units for
    :type application_name: str

    :returns: List of juju units
    :rtype: [juju.unit.Unit, juju.unit.Unit,...]
    """
    async with run_in_model(model_name) as model:
        return model.applications[application_name].units

get_units = sync_wrapper(async_get_units)


async def async_get_machines(model_name, application_name):
    """Return all the machines of a given application

    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application to retrieve units for
    :type application_name: str

    :returns: List of juju machines
    :rtype: [juju.machine.Machine, juju.machine.Machine,...]
    """
    async with run_in_model(model_name) as model:
        machines = []
        for unit in model.applications[application_name].units:
            machines.append(unit.machine)
        return machines

get_machines = sync_wrapper(async_get_machines)


def get_first_unit_name(model_name, application_name):
    """Return name of lowest numbered unit of given application

    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application
    :type application_name: str

    :returns: Name of lowest numbered unit
    :rtype: str
    """
    return get_units(model_name, application_name)[0].name


def get_app_ips(model_name, application_name):
    """Return public address of all units of an application

    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application
    :type application_name: str

    :returns: List of ip addresses
    :rtype: [str, str,...]
    """
    return [u.public_address for u in get_units(model_name, application_name)]


async def async_get_application_config(model_name, application_name):
    """Return application configuration

    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application
    :type application_name: str

    :returns: Dictionary of configuration
    :rtype: dict
    """
    async with run_in_model(model_name) as model:
        return await model.applications[application_name].get_config()

get_application_config = sync_wrapper(async_get_application_config)


async def async_set_application_config(model_name, application_name,
                                       configuration):
    """Set application configuration

    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application
    :type application_name: str
    :param configuration: Dictionary of configuration setting(s)
    :type configuration: dict
    :returns: None
    :rtype: None
    """
    async with run_in_model(model_name) as model:
        return await (model.applications[application_name]
                      .set_config(configuration))

set_application_config = sync_wrapper(async_set_application_config)


async def async_get_status(model_name):
    """Return full status

    :param model_name: Name of model to query.
    :type model_name: str

    :returns: dictionary of juju status
    :rtype: dict
    """
    async with run_in_model(model_name) as model:
        return await model.get_status()

get_status = sync_wrapper(async_get_status)


async def async_run_action(model_name, unit_name, action_name,
                           action_params=None):
    """Run action on given unit

    :param model_name: Name of model to query.
    :type model_name: str
    :param unit_name: Name of unit to run action on
    :type unit_name: str
    :param action_name: Name of action to run
    :type action_name: str
    :param action_params: Dictionary of config options for action
    :type action_params: dict
    :returns: Action object
    :rtype: juju.action.Action
    """
    async with run_in_model(model_name) as model:
        unit = get_unit_from_name(unit_name, model)
        action_obj = await unit.run_action(action_name, **action_params)
        await action_obj.wait()
        return action_obj

run_action = sync_wrapper(async_run_action)


async def async_run_action_on_leader(model_name, application_name, action_name,
                                     action_params=None):
    """Run action on lead unit of the given application

    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application
    :type application_name: str
    :param action_name: Name of action to run
    :type action_name: str
    :param action_params: Dictionary of config options for action
    :type action_params: dict
    :returns: Action object
    :rtype: juju.action.Action
    """
    async with run_in_model(model_name) as model:
        for unit in model.applications[application_name].units:
            is_leader = await unit.is_leader_from_status()
            if is_leader:
                action_obj = await unit.run_action(action_name,
                                                   **action_params)
                await action_obj.wait()
                return action_obj

run_action_on_leader = sync_wrapper(async_run_action_on_leader)


class UnitError(Exception):
    """Exception raised for units in error state
    """

    def __init__(self, units):
        message = "Units {} in error state".format(
            ','.join([u.entity_id for u in units]))
        super(UnitError, self).__init__(message)


class ServiceNotRunning(Exception):
    """Exception raised when service not running
    """

    def __init__(self, service):
        message = "Service {} not running".format(service)
        super(ServiceNotRunning, self).__init__(message)


def units_with_wl_status_state(model, state):
    """Return a list of unit which have a matching workload status

    :returns: Units in error state
    :rtype: [juju.Unit, ...]
    """
    matching_units = []
    for unit in model.units.values():
        wl_status = unit.workload_status
        if wl_status == state:
            matching_units.append(unit)
    return matching_units


def check_model_for_hard_errors(model):
    """Check model for any hard errors that should halt a deployment

       The only check currently implemented is checking for units in an
       error state

    :raises: UnitError
    """
    errored_units = units_with_wl_status_state(model, 'error')
    if errored_units:
        raise UnitError(errored_units)


def check_unit_workload_status(model, unit, state):
    """Check that the units workload status matches the supplied state.
       This function has the side effect of also checking for *any* units
       in an error state and aborting if any are found.

    :param model: Model object to check in
    :type model: juju.Model
    :param unit: Unit to check wl status of
    :type unit: juju.Unit
    :param state: Expected unit work load state
    :type state: str
    :raises: UnitError
    :returns: Whether units workload status matches desired state
    :rtype: bool
    """
    check_model_for_hard_errors(model)
    return unit.workload_status == state


def check_unit_workload_status_message(model, unit, message=None,
                                       prefixes=None):
    """Check that the units workload status message matches the supplied
       message or starts with one of the supplied prefixes. Raises an exception
       if neither prefixes or message is set. This function has the side effect
       of also checking for *any* units in an error state and aborting if any
       are found.

    :param model: Model object to check in
    :type model: juju.Model
    :param unit: Unit to check wl status of
    :type unit: juju.Unit
    :param message: Expected message text
    :type message: str
    :param prefixes: Prefixes to match message against
    :type prefixes: tuple
    :raises: ValueError, UnitError
    :returns: Whether message matches desired string
    :rtype: bool
    """
    check_model_for_hard_errors(model)
    if message:
        return unit.workload_status_message == message
    elif prefixes:
        return unit.workload_status_message.startswith(prefixes)
    else:
        raise ValueError("Must be called with message or prefixes")


async def async_wait_for_application_states(model_name, states=None,
                                            timeout=2700):
    """Wait for model to achieve the desired state

    Check the workload status and workload status message for every unit of
    every application. By default look for an 'active' workload status and a
    message that starts with one of the approved_message_prefixes.

    Bespoke statuses and messages can be passed in with states. states takes
    the form:

    {
        'app': {
            'workload-status': 'blocked',
            'workload-status-message': 'No requests without a prod'}
        'anotherapp': {
            'workload-status-message': 'Unit is super ready'}}


    :param model_name: Name of model to query.
    :type model_name: str
    :param states: States to look for
    :type states: dict
    :param timeout: Time to wait for status to be achieved
    :type timeout: int
    """
    approved_message_prefixes = ('ready', 'Ready', 'Unit is ready')

    if not states:
        states = {}
    async with run_in_model(model_name) as model:
        check_model_for_hard_errors(model)
        logging.info("Waiting for a unit to appear")
        await model.block_until(
            lambda: len(model.units) > 0
        )
        logging.info("Waiting for all units to be idle")
        await model.block_until(
            lambda: model.all_units_idle(), timeout=timeout)
        for application in model.applications:
            check_info = states.get(application, {})
            for unit in model.applications[application].units:
                logging.info("Checking workload status of {}".format(
                    unit.entity_id))
                await model.block_until(
                    lambda: check_unit_workload_status(
                        model,
                        unit,
                        check_info.get('workload-status', 'active')),
                    timeout=timeout)
                check_msg = check_info.get('workload-status-message')
                logging.info("Checking workload status message of {}".format(
                    unit.entity_id))
                if check_msg:
                    prefixes = (check_msg)
                else:
                    prefixes = approved_message_prefixes
                await model.block_until(
                    lambda: check_unit_workload_status_message(
                        model,
                        unit,
                        prefixes=prefixes),
                    timeout=timeout)

wait_for_application_states = sync_wrapper(async_wait_for_application_states)


async def async_block_until_all_units_idle(model_name, timeout=2700):
    """Block until all units in the given model are idle

    :param model_name: Name of model to query.
    :type model_name: str
    :param timeout: Time to wait for status to be achieved
    :type timeout: int
    """
    async with run_in_model(model_name) as model:
        await model.block_until(
            lambda: model.all_units_idle(), timeout=timeout)

block_until_all_units_idle = sync_wrapper(async_block_until_all_units_idle)


async def async_block_until_service_status(model_name, unit_name, services,
                                           target_status, timeout=2700):
    """Block until all services on the unit are in the desired state (stopped
       or running)

    :param model_name: Name of model to query.
    :type model_name: str
    :param unit_name: Name of unit to run action on
    :type unit_name: str
    :param services: List of services to check
    :type services: []
    :param target_status: State services should be in (stopped or running)
    :type target_status: str
    :param timeout: Time to wait for status to be achieved
    :type timeout: int
    """
    async def _check_service():
        for service in services:
            out = await async_run_on_unit(
                model_name,
                unit_name,
                "pidof -x {}".format(service),
                timeout=timeout)
            response_size = len(out['Stdout'].strip())
            if target_status == "running" and response_size == 0:
                return False
            elif target_status == "stopped" and response_size > 0:
                return False
        return True
    async with run_in_model(model_name):
        await async_block_until(_check_service, timeout=timeout)

block_until_service_status = sync_wrapper(async_block_until_service_status)


def get_actions(model_name, application_name):
    """Get the actions an applications supports

    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application
    :type application_name: str
    :returns: Dictionary of actions and their descriptions
    :rtype: dict
    """
    # libjuju has not implemented get_actions yet
    # https://github.com/juju/python-libjuju/issues/226
    cmd = ['juju', 'actions', '-m', model_name, application_name,
           '--format', 'yaml']
    return yaml.load(subprocess.check_output(cmd))


async def async_get_current_model():
    """Return the current active model name

    Connect to the current active model and return its name.

    :returns: String curenet model name
    :rtype: str
    """

    model = Model()
    await model.connect()
    model_name = model.info.name
    await model.disconnect()
    return model_name


get_current_model = sync_wrapper(async_get_current_model)


async def async_block_until(*conditions, timeout=None, wait_period=0.5,
                            loop=None):
    """Return only after all async conditions are true.

    Based on juju.utils.block_until which currently does not support
    async methods as conditions.

    :param conditions: Functions to evaluate.
    :type conditions: functions
    :param timeout: Timeout in secounds
    :type timeout: float
    :param wait_period: Time to wait between re-assing conditions.
    :type wait_period: float
    :param loop: The evnt loop to use
    :type loop: An event loop
    """

    async def _block():
        while True:
            evaluated = []
            for c in conditions:
                result = await c()
                evaluated.append(result)
            if all(evaluated):
                return
            else:
                await asyncio.sleep(wait_period, loop=loop)
    await asyncio.wait_for(_block(), timeout, loop=loop)


async def async_block_until_file_ready(model_name, application_name,
                                       remote_file, check_function,
                                       timeout=2700):
    """Block until the check_function passes against the contents of the given
       file on all units of the application

    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application
    :type application_name: str
    :param remote_file: Remote path of file to transfer
    :type remote_file: str
    :param check_function: Function to use to check if file is ready, must take
                           exactly one argument which is the file contents.
    :type check_function: function
    :param timeout: Time to wait for contents to appear in file
    :type timeout: float
    """
    async def _check_file():
        file_name = os.path.basename(remote_file)
        units = model.applications[application_name].units
        with tempfile.TemporaryDirectory() as tmpdir:
            for unit in units:
                try:
                    await unit.scp_from(remote_file, tmpdir)
                    with open(os.path.join(tmpdir, file_name), 'r') as lf:
                        contents = lf.read()
                    if not check_function(contents):
                        return False
                # libjuju throws a generic error for scp failure. So we cannot
                # differentiate between a connectivity issue and a target file
                # not existing error. For now just assume the latter.
                except JujuError as e:
                    return False
            else:
                return True

    async with run_in_model(model_name) as model:
        await async_block_until(_check_file, timeout=timeout)


async def async_block_until_file_has_contents(model_name, application_name,
                                              remote_file, expected_contents,
                                              timeout=2700):
    """Block until the expected_contents are in the given file on all units of
       the application

    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application
    :type application_name: str
    :param remote_file: Remote path of file to transfer
    :type remote_file: str
    :param expected_contents: String to look for in file
    :type expected_contents: str
    :param timeout: Time to wait for contents to appear in file
    :type timeout: float
    """
    def f(x):
        return expected_contents in x
    return await async_block_until_file_ready(model_name, application_name,
                                              remote_file, f, timeout)

block_until_file_has_contents = sync_wrapper(
    async_block_until_file_has_contents)


async def async_block_until_oslo_config_entries_match(model_name,
                                                      application_name,
                                                      remote_file,
                                                      expected_contents,
                                                      timeout=2700):
    """Block until the expected_contents are in the given file on all units of
       the application

    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application
    :type application_name: str
    :param remote_file: Remote path of file to transfer
    :type remote_file: str
    :param expected_contents: The key values pairs in their corresponding
                              sections to be looked for in the remote_file
    :type expected_contents: {}
    :param timeout: Time to wait for contents to appear in file
    :type timeout: float

    For example to check for,

        [DEFAULT]
        debug = False

        [glance_store]
        filesystem_store_datadir = /var/lib/glance/images/
        default_store = file

    use:

        expected_contents = {
            'DEFAULT': {
                'debug': ['False']},
            'glance_store': {
                'filesystem_store_datadir': ['/var/lib/glance/images/'],
                 'default_store': ['file']}}
    """
    def f(x):
        # Writing out the file that was just read is suboptimal
        with tempfile.NamedTemporaryFile(mode='w', delete=True) as fp:
            fp.write(x)
            fp.seek(0)
            sections = {}
            parser = cfg.ConfigParser(fp.name, sections)
            parser.parse()
            for section, entries in expected_contents.items():
                for key, value in entries.items():
                    if sections.get(section, {}).get(key) != value:
                        return False
        return True
    return await async_block_until_file_ready(model_name, application_name,
                                              remote_file, f, timeout)


block_until_oslo_config_entries_match = sync_wrapper(
    async_block_until_oslo_config_entries_match)


async def async_block_until_services_restarted(model_name, application_name,
                                               mtime, services, timeout=2700):
    """Block until the given services have a start time later then mtime
    :param model_name: Name of model to query.
    :type model_name: str
    :param application_name: Name of application
    :type application_name: str
    :param mtime: Time in seconds since Epoch to check against
    :type mtime: int
    :param services: Listr of services to check restart time of
    :type services: []
    :param timeout: Time to wait for services to be restarted
    :type timeout: float
    """
    async def _check_service():
        units = model.applications[application_name].units
        for unit in units:
            for service in services:
                try:
                    svc_mtime = await async_get_unit_service_start_time(
                        model_name,
                        unit.entity_id,
                        service)
                except ServiceNotRunning:
                    return False
                if svc_mtime < mtime:
                    return False
        return True
    async with run_in_model(model_name) as model:
        await async_block_until(_check_service, timeout=timeout)


block_until_services_restarted = sync_wrapper(
    async_block_until_services_restarted)


async def async_block_until_unit_wl_status(model_name, unit_name, status,
                                           timeout=2700):
    """Block until the given unit has the desired workload status
    :param model_name: Name of model to query.
    :type model_name: str
    :param unit_name: Name of unit to run action on
    :type unit_name: str
    :param status: Status to wait for (active, maintenance etc)
    :type status: str
    :param timeout: Time to wait for unit to achieved desired status
    :type timeout: float
    """
    async with run_in_model(model_name) as model:
        unit = get_unit_from_name(unit_name, model)
        await model.block_until(
            lambda: unit.workload_status == status,
            timeout=timeout
        )

block_until_unit_wl_status = sync_wrapper(
    async_block_until_unit_wl_status)


async def async_get_relation_id(model_name, application_name,
                                remote_application_name,
                                remote_interface_name=None):
    """
    Get relation id of relation from model

    :param model_name: Name of model to operate on
    :type model_name: str
    :param application_name: Name of application on this side of relation
    :type application_name: str
    :param remote_application_name: Name of application on other side of
                                    relation
    :type remote_application_name: str
    :param remote_interface_name: Name of interface on remote end of relation
    :type remote_interface_name: Optional(str)
    :returns: Relation id of relation if found or None
    :rtype: any
    """
    async with run_in_model(model_name) as model:
        for rel in model.applications[application_name].relations:
            spec = '{}'.format(remote_application_name)
            if remote_interface_name is not None:
                spec += ':{}'.format(remote_interface_name)
            if rel.matches(spec):
                return(rel.id)

get_relation_id = sync_wrapper(async_get_relation_id)


def main():
    # Run the deploy coroutine in an asyncio event loop, using a helper
    # that abstracts loop creation and teardown.
    print("Current applications: {}".format(", ".join(loop.run(deployed()))))


if __name__ == '__main__':
    main()
