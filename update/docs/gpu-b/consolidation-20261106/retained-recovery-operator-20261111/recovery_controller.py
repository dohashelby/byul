"""회수 전용 controller 후보. import/default 실행은 AWS를 호출하지 않는다.

실제 private loader/invoker가 고정한 driver/glue/packet을 명시적으로 공급해야 한다.
현재 저장된 후보 packet은 not_ready이며 실제 운영 진입점·staging은 별도다.
"""
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

IID = "i-05e8da8ea99593196"
CODEC_SHA = "6de3190f37d333bd1055e6abe706addb2a606405f701cf710712150852e1591f"
GLUE_SHA = "1077c5270ed530d330d9916356c621279893a5161b44cc8260ada804b27f707f"


class RecoveryError(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise RecoveryError(message)


class Controller:
    def __init__(self, driver, glue, packet, codec_raw, clock=time):
        require(packet.get("not_ready") is False, "FINAL_RECOVERY_PACKET_NOT_READY")
        require(packet.get("purpose") == "retained-R8-envelope-recovery-only", "RECOVERY_ONLY_PACKET_REQUIRED")
        require(driver.IID == IID and driver.PACKET is packet, "SAME_FIXED_DRIVER_PACKET_REQUIRED")
        require(glue.CODEC_SHA == CODEC_SHA and hashlib.sha256(codec_raw).hexdigest() == CODEC_SHA,
                "UNCHANGED_CODEC_REQUIRED")
        require(packet["glue_sha256"] == GLUE_SHA and packet["copy_helper_sha256"] == glue.COPY_SHA,
                "EXACT_REVIEWED_GLUE_AND_GET_REQUIRED")
        request_id = packet["collector_request_id"]
        require(str(uuid.UUID(request_id)) == request_id, "PACKET_FIXED_COLLECTOR_UUID_REQUIRED")
        self.driver, self.glue, self.packet = driver, glue, packet
        self.codec_raw, self.clock = codec_raw, clock
        self.out = Path(driver.OUT)
        require(str(self.out) == packet["private_path"], "SAME_PRIVATE_OUTPUT_REQUIRED")
        self.health_complete = False
        self.encode_submitted = False
        self.encoder_terminal_safe = False
        self.envelope_recovered = False

    def read(self, name):
        raw = (self.out / name).read_bytes()
        require(len(raw) <= 2 * 1024 * 1024, "BOUNDED_ORIGINAL_CONTROL_JSON_REQUIRED")
        return json.loads(raw)

    def accepted(self, record, expected_request):
        require(record["request"] == expected_request
                and record["request_sha256"] == self.glue.request_sha(expected_request),
                "EXACT_ORIGINAL_REQUEST_SHA_REQUIRED")
        command = record["response"]["Command"]
        cid = command["CommandId"]
        require(str(uuid.UUID(cid)) == cid and command["InstanceIds"] == [IID],
                "EXACT_ACCEPTED_COMMAND_TARGET_REQUIRED")
        require(command.get("DocumentName") == expected_request["DocumentName"]
                and command.get("Parameters") == expected_request["Parameters"],
                "EXACT_ACCEPTED_DOCUMENT_PARAMETERS_REQUIRED")
        return cid

    def terminal_matches(self, terminal, cid):
        return (terminal.get("CommandId") == cid and terminal.get("InstanceId") == IID
                and type(terminal.get("ResponseCode")) is int)

    def observe_health(self):
        binding = self.driver.health()
        record, terminal = self.read("health-submission.json"), self.read("health-terminal.json")
        cid = self.accepted(record, record["request"])
        require(self.terminal_matches(terminal, cid) and terminal["Status"] == "Success"
                and terminal["ResponseCode"] == 0, "POSITIVE_HEALTH_TERMINAL_REQUIRED")
        lines = terminal["StandardOutputContent"].splitlines()
        require(len(lines) == 2, "COMPLETE_ORIGINAL_HEALTH_CHANNELS_REQUIRED")
        health, bound = (json.loads(line) for line in lines)
        require(bound == {"original_health_binding": binding}, "SAME_ORIGINAL_HEALTH_BINDING_REQUIRED")
        require(health["output"] == self.packet["remote_health_output"]
                and binding["path"] == health["output"] + "/health-receipt.json",
                "SAME_NEW_HEALTH_OUTPUT_REQUIRED")
        require(health["health_complete"] is True and health["PG_terminal"] is True
                and health["physical_GPU_count"] == 1 and health["prior_GPU_processes"] == 0
                and health["timer_reset"] is False and health["engine_created"] is False
                and health["factory_executed"] is False, "ORIGINAL_HEALTH_GUARDS_REQUIRED")
        boot = self.glue.uuid_text(health["boot_id"])
        require(boot != self.glue.REFERENCE["original_boot_id"], "NEW_RECOVERY_BOOT_REQUIRED")
        self.driver.put("recovery-health-joined.json", {
            "at": self.driver.now(), "health_CommandId": cid, "health_binding": binding,
            "recovery_boot_id": boot, "original_experiment_boot_reused": False,
        })
        self.health_complete = True
        return boot

    def submit_encoder(self):
        request = self.glue.build_encode_request(self.codec_raw, self.packet["collector_request_id"])
        self.driver.uncertainty_barrier("before-retained-envelope-encode")
        require(self.driver.states()[IID] == "running" and not self.driver.active_commands(),
                "RUNNING_WITH_NO_ACTIVE_COMMAND_REQUIRED")
        nodes = self.driver.SSM.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [IID]}])["InstanceInformationList"]
        require(len(nodes) == 1 and nodes[0]["PingStatus"] == "Online", "SELECTED_SSM_ONLINE_REQUIRED")
        record = {"at": self.driver.now(), "phase": "retained-envelope-encode", "request": request,
                  "request_sha256": self.glue.request_sha(request)}
        self.driver.put("retained-envelope-encode-intent.json", record)
        response = self.driver.SSM.send_command(**request)
        self.encode_submitted = True
        record = {**record, "at": self.driver.now(), "response": response}
        self.driver.put("retained-envelope-encode-submission.json", record)
        cid = self.accepted(record, request)
        return record, cid

    def collect(self):
        boot = self.observe_health()
        record, cid = self.submit_encoder()
        try:
            terminal = self.driver.wait_command("retained-envelope-encode", cid, 120)
        except BaseException as original:
            self.encoder_terminal_safe = False
            try:
                path = self.out / "retained-envelope-encode-terminal.json"
                if path.exists():
                    saved = self.read(path.name)
                    # 고정 encoder는 subprocess를 만들지 않는다. 정확한 Failed1만 보존한다.
                    self.encoder_terminal_safe = (self.terminal_matches(saved, cid)
                                                  and saved.get("Status") == "Failed"
                                                  and saved.get("ResponseCode") == 1)
            except BaseException as diagnostic_error:
                self.encoder_terminal_safe = False
                original.add_note("Encoder terminal diagnosis failed: " + repr(diagnostic_error))
            raise
        self.encoder_terminal_safe = (self.terminal_matches(terminal, cid)
                                      and terminal.get("Status") == "Success"
                                      and terminal.get("ResponseCode") == 0)
        validated = self.glue.join_encode_terminal(
            self.codec_raw, self.packet["collector_request_id"], boot, record, terminal)
        self.driver.put("trusted-encoder-receipt.json", {
            "at": self.driver.now(), "CommandId": validated.command_id,
            "original_request_sha256": validated.request_sha256,
            "original_stdout_sha256": validated.stdout_sha256,
            "receipt": validated.receipt(), "actual_archive_restored": False,
        })
        folder = self.out / "actual-envelope"
        folder.mkdir(mode=0o700)
        self.glue.invoke_existing_get_once(
            validated, self.driver.run_transfer_group, sys.executable,
            self.out / "copy_helper-exact-download-v1.py",
            folder / "GB2026111112-transfer.json", folder / "copy.jsonl")
        self.envelope_recovered = True
        self.driver.put("exact-envelope-recovered.json", {
            "at": self.driver.now(), "CommandId": validated.command_id,
            "receipt": validated.receipt(), "original_zip_restored": False,
            "whole_archive_CRC_or_product_success_claimed": False,
        })

    def cleanup(self, original_error):
        self.driver.uncertainty_barrier(
            "before-recovery-cleanup", known_uncertain=bool(getattr(original_error, "mutation_uncertain", False)))
        require(self.health_complete and (not self.encode_submitted or self.encoder_terminal_safe),
                "GUEST_TERMINAL_UNPROVEN_NO_FOLLOWING_MUTATION")
        require(not self.driver.active_commands(), "ACTIVE_COMMAND_NO_STOP")
        current = self.driver.states()[IID]
        if current == "running":
            self.driver.normal_stop_once()
        else:
            require(current in ("stopped", "stopping"), "UNEXPECTED_SELECTED_STATE")
        deadline = self.clock.monotonic() + 180
        while True:
            state = self.driver.states()[IID]
            if state == "stopped":
                break
            require(state in ("running", "stopping"), "UNEXPECTED_SELECTED_STATE")
            require(self.clock.monotonic() < deadline, "STOPPED_NOT_YET_OBSERVED")
            self.clock.sleep(3)
        self.driver.put("recovery-selected-stopped.json", {
            "at": self.driver.now(), "InstanceId": IID, "state": state,
            "collector_request_id": self.packet["collector_request_id"],
            "exact_off_transition_UTC": None, "envelope_recovered": self.envelope_recovered,
            "original_zip_restored": False, "product_success": False,
        })

    def run_once(self):
        # 배타 intent가 있으므로 동일 private에서 두 번째 호출은 어떤 API보다 먼저 거부된다.
        self.driver.put("retained-envelope-collect-once-intent.json", {
            "at": self.driver.now(), "collector_request_id": self.packet["collector_request_id"],
            "scope": "retained GB12 recovery only; source/model/whole execution excluded",
        })
        original = None
        try:
            self.collect()
        except BaseException as error:
            original = error
            try:
                self.driver.put("retained-envelope-original-failure.json", {
                    "at": self.driver.now(), "type": type(error).__name__, "message": str(error),
                    "mutation_uncertain": bool(getattr(error, "mutation_uncertain", False)),
                })
            except BaseException as secondary:
                error.add_note("Failure record preservation failed: " + repr(secondary))
        try:
            self.cleanup(original)
        except BaseException as cleanup_error:
            try:
                self.driver.put("retained-envelope-cleanup-failure.json", {
                    "at": self.driver.now(), "type": type(cleanup_error).__name__,
                    "message": str(cleanup_error), "normal_stop_retried": False,
                })
            except BaseException as secondary:
                cleanup_error.add_note("Cleanup record preservation failed: " + repr(secondary))
            if original is None:
                raise
            original.add_note("Independent cleanup failure preserved: " + repr(cleanup_error))
        if original is not None:
            raise original.with_traceback(original.__traceback__)
        return {"envelope_recovered": self.envelope_recovered, "original_zip_restored": False,
                "product_success": False, "scope": "data-only envelope collection"}
