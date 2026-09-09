import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from asyncua import Server, ua, uamethod
from asyncua.common.instantiate_util import instantiate
from asyncua.common.statemachine import FiniteStateMachine, State

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("raspi-opcua")

TEMP_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
NODESET_PATH = Path(__file__).parent / "Opc.Ua.MachineVision.NodeSet2.xml"
MACHINE_VISION_NAMESPACE_URI = "http://opcfoundation.org/UA/MachineVision"
RESULT_TYPE_NODEID = 2002  # 1:ResultType im Machine-Vision-Nodeset
JOB_STARTED_EVENT_TYPE_NODEID = 1013  # 1:JobStartedEventType
ACQUISITION_DONE_EVENT_TYPE_NODEID = 1025  # 1:AcquisitionDoneEventType
RESULT_READY_EVENT_TYPE_NODEID = 1024  # 1:ResultReadyEventType

# AutomaticModeStateMachine-States sind nur am Typ VisionAutomaticModeStateMachineType
# (ns=1;i=1021) deklariert, nicht als Mandatory-Kinder der Instanz -> beim
# Instanziieren von VisionSystemType werden sie nicht kopiert. Es sind daher
# feste, gemeinsame Knoten im MachineVision-Namespace, direkt per NodeId zu holen.
INITIALIZED_STATE_NODEID = 5056  # 1:Initialized, StateNumber=5
READY_STATE_NODEID = 5057  # 1:Ready, StateNumber=6
SINGLE_EXECUTION_STATE_NODEID = 5058  # 1:SingleExecution, StateNumber=7


def read_cpu_temp() -> float:
    try:
        return int(TEMP_PATH.read_text().strip()) / 1000.0
    except (OSError, ValueError):
        return float("nan")


async def _load_state(node) -> State:
    browse_name = (await node.read_browse_name()).Name
    state_number = await (await node.get_child(["StateNumber"])).read_value()
    return State(id=None, name=browse_name, number=state_number, node=node)


async def _bind_state_machine(server: Server, node, name: str) -> FiniteStateMachine:
    """Bindet eine FiniteStateMachine an einen bereits vorhandenen
    State-Machine-Knoten (aus der Nodeset-Instanziierung), statt per
    install() einen neuen anzulegen."""
    fsm = FiniteStateMachine(server, parent=node, name=name)
    fsm._state_machine_node = node
    await fsm.init(node)
    return fsm


async def main():
    server = Server()
    await server.init()

    server.set_endpoint("opc.tcp://0.0.0.0:4840/raspi/server/")
    server.set_server_name("Raspberry Pi OPC UA Server")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    idx = await server.register_namespace("http://launch-rm.de/raspi")

    device = await server.nodes.objects.add_object(idx, "RaspiDevice")

    cpu_temp = await device.add_variable(
        idx, "CpuTemperature", 0.0, ua.VariantType.Double
    )
    counter = await device.add_variable(idx, "Counter", 0, ua.VariantType.Int64)
    setpoint = await device.add_variable(idx, "Setpoint", 0.0, ua.VariantType.Double)

    await setpoint.set_writable()

    _log.info("Importiere Machine-Vision-Nodeset von %s", NODESET_PATH)
    await server.import_xml(str(NODESET_PATH))
    mv_idx = await server.get_namespace_index(MACHINE_VISION_NAMESPACE_URI)

    vision_system_type = await server.nodes.base_object_type.get_child(
        f"{mv_idx}:VisionSystemType"
    )
    vision_system = await server.nodes.objects.add_object(
        idx, "VisionSystem", objecttype=vision_system_type
    )

    await server.nodes.server.add_reference(
        vision_system, ua.ObjectIds.HasNotifier, forward=True
    )
    await vision_system.set_event_notifier([ua.EventNotifier.SubscribeToEvents])

    result_management = await vision_system.get_child(f"{mv_idx}:ResultManagement")
    results_folder = await result_management.get_child(f"{mv_idx}:Results")

    result_type = server.get_node(ua.NodeId(RESULT_TYPE_NODEID, mv_idx))
    result_nodes = await instantiate(
        results_folder, result_type, bname=f"{idx}:CpuTemperatureResult"
    )
    temperature_result = result_nodes[0]
    result_content = await temperature_result.get_child(f"{mv_idx}:ResultContent")
    await result_content.write_attribute(
        ua.AttributeIds.DataType,
        ua.DataValue(ua.Variant(ua.NodeId(ua.ObjectIds.Double), ua.VariantType.NodeId)),
    )

    # --- Phase 3 (Smoke-Test): State Machine, StartSingleJob, Events ---

    vision_state_machine_node = await vision_system.get_child(f"{mv_idx}:VisionStateMachine")
    automatic_state_machine_node = await vision_state_machine_node.get_child(
        f"{mv_idx}:AutomaticModeStateMachine"
    )
    vision_fsm = await _bind_state_machine(server, vision_state_machine_node, "VisionStateMachine")
    automatic_fsm = await _bind_state_machine(
        server, automatic_state_machine_node, "AutomaticModeStateMachine"
    )

    state_halted = await _load_state(await vision_state_machine_node.get_child(f"{mv_idx}:Halted"))
    state_operational = await _load_state(
        await vision_state_machine_node.get_child(f"{mv_idx}:Operational")
    )
    state_initialized = await _load_state(server.get_node(ua.NodeId(INITIALIZED_STATE_NODEID, mv_idx)))
    state_ready = await _load_state(server.get_node(ua.NodeId(READY_STATE_NODEID, mv_idx)))
    state_single_execution = await _load_state(
        server.get_node(ua.NodeId(SINGLE_EXECUTION_STATE_NODEID, mv_idx))
    )

    await vision_fsm.change_state(state_halted, event_msg="Preoperational -> Halted")
    await vision_fsm.change_state(state_operational, event_msg="Halted -> Operational")
    await automatic_fsm.change_state(state_initialized, event_msg="Startup -> Initialized")
    await automatic_fsm.change_state(state_ready, event_msg="Initialized -> Ready")

    ev_job_started = await server.get_event_generator(
        ua.NodeId(JOB_STARTED_EVENT_TYPE_NODEID, mv_idx), vision_system
    )
    ev_acquisition_done = await server.get_event_generator(
        ua.NodeId(ACQUISITION_DONE_EVENT_TYPE_NODEID, mv_idx), vision_system
    )
    ev_result_ready = await server.get_event_generator(
        ua.NodeId(RESULT_READY_EVENT_TYPE_NODEID, mv_idx), vision_system
    )

    hello_world_result = (
        await instantiate(results_folder, result_type, bname=f"{idx}:HelloWorldResult")
    )[0]
    hw_result_content = await hello_world_result.get_child(f"{mv_idx}:ResultContent")
    await hw_result_content.write_attribute(
        ua.AttributeIds.DataType,
        ua.DataValue(ua.Variant(ua.NodeId(ua.ObjectIds.String), ua.VariantType.NodeId)),
    )
    hw_result_id = await hello_world_result.get_child(f"{mv_idx}:ResultId")
    hw_is_partial = await hello_world_result.get_child(f"{mv_idx}:IsPartial")
    hw_result_state = await hello_world_result.get_child(f"{mv_idx}:ResultState")
    hw_internal_recipe_id = await hello_world_result.get_child(f"{mv_idx}:InternalRecipeId")
    hw_internal_configuration_id = await hello_world_result.get_child(
        f"{mv_idx}:InternalConfigurationId"
    )
    hw_job_id = await hello_world_result.get_child(f"{mv_idx}:JobId")
    hw_creation_time = await hello_world_result.get_child(f"{mv_idx}:CreationTime")

    job_counter = 0

    async def _run_hello_world_job(job_id: str) -> None:
        await automatic_fsm.change_state(state_single_execution, event_msg="Ready -> SingleExecution")
        await ev_job_started.trigger(message=f"Job {job_id} started")

        now = datetime.now(timezone.utc)
        payload = json.dumps(
            {
                "schema": "wsc.vision.test/1",
                "jobId": job_id,
                "creationTime": now.isoformat(timespec="seconds"),
                "message": "Hello World",
                "time": now.strftime("%H:%M:%S"),
            }
        )

        await ev_acquisition_done.trigger(message="Acquisition done")

        await hw_result_id.write_value(f"res-{job_id}", ua.VariantType.String)
        await hw_is_partial.write_value(False, ua.VariantType.Boolean)
        await hw_result_state.write_value(0, ua.VariantType.Int32)
        await hw_internal_recipe_id.write_value("", ua.VariantType.String)
        await hw_internal_configuration_id.write_value("", ua.VariantType.String)
        await hw_job_id.write_value(job_id, ua.VariantType.String)
        await hw_creation_time.write_value(now, ua.VariantType.DateTime)
        await hw_result_content.write_value(payload, ua.VariantType.String)

        await ev_result_ready.trigger(message=f"Result for {job_id} ready")
        await automatic_fsm.change_state(state_ready, event_msg="SingleExecution -> Ready")

    @uamethod
    async def start_single_job(parent, meas_id, part_id, recipe_id, product_id, parameters):
        nonlocal job_counter
        job_counter += 1
        job_id = f"job-{job_counter}"
        asyncio.create_task(_run_hello_world_job(job_id))
        return [ua.Variant(job_id, ua.VariantType.String), ua.Variant(0, ua.VariantType.Int32)]

    start_single_job_node = await automatic_state_machine_node.get_child(f"{mv_idx}:StartSingleJob")
    server.link_method(start_single_job_node, start_single_job)

    _log.info("Server startet auf %s", server.endpoint.geturl())

    async with server:
        n = 0
        while True:
            n += 1
            temp = read_cpu_temp()
            await counter.write_value(n)
            await cpu_temp.write_value(temp)
            await result_content.write_value(temp, ua.VariantType.Double)
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())