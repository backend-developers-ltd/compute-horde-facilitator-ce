import csv
import io
import json
from datetime import datetime
from typing import NamedTuple

import pytest
from asgiref.sync import sync_to_async
from compute_horde.receipts.models import JobFinishedReceipt, JobStartedReceipt
from constance import config

from ..models import Channel, Validator
from ..schemas import JobFinishedReceiptPayload, JobStartedReceiptPayload
from ..tasks import fetch_receipts, sync_metagraph

RAW_RECEIPT_PAYLOAD_1 = """{"payload":{"job_uuid":"fcc0f984-766f-4c10-a535-f6a7919af10a","miner_hotkey":"5G1m7GHoMW5GSdPv6pSpz4xi8CTXiLuCpZWEGZVaRdsB7zGZ","validator_hotkey":"5H9BnRTvaSLDzwVSHHFxLL9zU15CAf4L94fXkuvZiUTBQJ4Q","executor_class":"spin_up-4min.gpu-24gb","time_accepted":"2024-07-09T19:17:22.865041+00:00","max_timeout":30},"validator_signature":"0xc2d8695ebd8b862e0d968c57ef7b69d50e5169e77e629c6c15a7365d51b929044353bf492d5e2769c48556b09034da5df13c84741c9bfef628cddf9326104188","miner_signature":"0x1ae85bca0a9314349cd36003b552a9fbd4ba5a8edc1abae1e9789c36eaf081046d085b9ea62c924332bdfd7adf2757540e3bc586ceb3447223cd1b2f067dba89"}"""
RAW_RECEIPT_PAYLOAD_2 = """{"payload":{"job_uuid":"f6939ad1-d46d-4b4d-b022-2aac6b72428f","miner_hotkey":"5G1m7GHoMW5GSdPv6pSpz4xi8CTXiLuCpZWEGZVaRdsB7zGZ","validator_hotkey":"5H9BnRTvaSLDzwVSHHFxLL9zU15CAf4L94fXkuvZiUTBQJ4Q","time_started":"2024-07-09T19:17:22.865280+00:00","time_took_us":30000000,"score_str":"0.1234"},"validator_signature":"0x7cf4ad9d3f6f94403a6267a31bbe02f4c5f36ec7493381de40b96a166e897b3b8ab0c2bcbec9432d56087eb1478d66f161f5bc4d283948c23c259074130e5e89","miner_signature":"0x4423a415eb2538f54d71fb9389fe8a845ea5cb5a7d41bbbbb9aa7cb537ed544fde3c4247e914446052a5a51142805c0ce562777d3aaf3751a55ee6e6a2fcb387"}"""

MINER_HOTKEY = "5G1m7GHoMW5GSdPv6pSpz4xi8CTXiLuCpZWEGZVaRdsB7zGZ"
PAYLOAD_2_MINER_SIGNATURE = "0x4423a415eb2538f54d71fb9389fe8a845ea5cb5a7d41bbbbb9aa7cb537ed544fde3c4247e914446052a5a51142805c0ce562777d3aaf3751a55ee6e6a2fcb387"
PAYLOAD_2_VALIDATOR_SIGNATURE = "0x7cf4ad9d3f6f94403a6267a31bbe02f4c5f36ec7493381de40b96a166e897b3b8ab0c2bcbec9432d56087eb1478d66f161f5bc4d283948c23c259074130e5e89"


class MockedAxonInfo(NamedTuple):
    is_serving: bool
    ip: str = ""
    port: int = 0


class MockedNeuron(NamedTuple):
    hotkey: str
    axon_info: MockedAxonInfo
    stake: float


validator_params = dict(
    axon_info=MockedAxonInfo(is_serving=False),
    stake=1.0,
)

miner_params = dict(
    axon_info=MockedAxonInfo(is_serving=True),
    stake=0.0,
)


@pytest.mark.django_db(transaction=True)
def test__sync_metagraph__activation(monkeypatch):
    import bittensor

    validators = Validator.objects.bulk_create(
        [
            Validator(ss58_address="remains_active", is_active=True),
            Validator(ss58_address="is_deactivated", is_active=True),
            Validator(ss58_address="remains_inactive", is_active=False),
            Validator(ss58_address="is_activated", is_active=False),
        ]
    )

    class MockedMetagraph:
        def __init__(self, *args, **kwargs):
            self.neurons = [
                MockedNeuron(hotkey="remains_active", **validator_params),
                MockedNeuron(hotkey="is_deactivated", **miner_params),
                MockedNeuron(hotkey="remains_inactive", **miner_params),
                MockedNeuron(hotkey="is_activated", **validator_params),
                MockedNeuron(hotkey="new_validator", **validator_params),
                MockedNeuron(hotkey="new_miner", **miner_params),
            ]

    with monkeypatch.context() as mp:
        mp.setattr(bittensor, "metagraph", MockedMetagraph)
        sync_metagraph()

    validators = Validator.objects.order_by("id").values_list("ss58_address", "is_active")
    assert list(validators) == [
        tuple(d.values())
        for d in [
            dict(ss58_address="remains_active", is_active=True),
            dict(ss58_address="is_deactivated", is_active=False),
            dict(ss58_address="remains_inactive", is_active=False),
            dict(ss58_address="is_activated", is_active=True),
            dict(ss58_address="new_validator", is_active=True),
        ]
    ]


@pytest.mark.django_db(transaction=True)
def test__sync_metagraph__limit__no_our_validator(monkeypatch):
    """When there is validator limit"""

    import bittensor

    class MockedMetagraph:
        def __init__(self, *args, **kwargs):
            self.neurons = [MockedNeuron(hotkey=str(i), **(validator_params | {"stake": 10 * i})) for i in range(1, 33)]

    config.VALIDATORS_LIMIT = 4

    with monkeypatch.context() as mp:
        mp.setattr(bittensor, "metagraph", MockedMetagraph)
        sync_metagraph()

    validators = Validator.objects.order_by("ss58_address").values_list("ss58_address")
    assert list(validators) == [
        tuple(d.values())
        for d in [
            dict(ss58_address="29"),
            dict(ss58_address="30"),
            dict(ss58_address="31"),
            dict(ss58_address="32"),
        ]
    ]


@pytest.mark.django_db(transaction=True)
def test__sync_metagraph__limit_and_our_validator__wrong(monkeypatch):
    """When there is validator limit and ours is not in validators list"""

    import bittensor

    class MockedMetagraph:
        def __init__(self, *args, **kwargs):
            self.neurons = [MockedNeuron(hotkey=str(i), **(validator_params | {"stake": 10 * i})) for i in range(1, 33)]

    config.VALIDATORS_LIMIT = 4
    config.OUR_VALIDATOR_SS58_ADDRESS = "99"

    with monkeypatch.context() as mp:
        mp.setattr(bittensor, "metagraph", MockedMetagraph)
        sync_metagraph()

    validators = Validator.objects.order_by("ss58_address").values_list("ss58_address")
    assert list(validators) == [
        tuple(d.values())
        for d in [
            dict(ss58_address="29"),
            dict(ss58_address="30"),
            dict(ss58_address="31"),
            dict(ss58_address="32"),
        ]
    ]


@pytest.mark.django_db(transaction=True)
def test__sync_metagraph__limit_and_our_validator__inside_limit(monkeypatch):
    """When there is validator limit and ours is one of best validators"""

    import bittensor

    class MockedMetagraph:
        def __init__(self, *args, **kwargs):
            self.neurons = [MockedNeuron(hotkey=str(i), **(validator_params | {"stake": 10 * i})) for i in range(1, 33)]

    config.VALIDATORS_LIMIT = 4
    config.OUR_VALIDATOR_SS58_ADDRESS = "30"

    with monkeypatch.context() as mp:
        mp.setattr(bittensor, "metagraph", MockedMetagraph)
        sync_metagraph()

    validators = Validator.objects.order_by("ss58_address").values_list("ss58_address")
    assert list(validators) == [
        tuple(d.values())
        for d in [
            dict(ss58_address="29"),
            dict(ss58_address="30"),
            dict(ss58_address="31"),
            dict(ss58_address="32"),
        ]
    ]


@pytest.mark.django_db(transaction=True)
def test__sync_metagraph__limit_and_our_validator__outside_limit(monkeypatch):
    """When there is validator limit and ours is not one of best validators"""

    import bittensor

    class MockedMetagraph:
        def __init__(self, *args, **kwargs):
            self.neurons = [MockedNeuron(hotkey=str(i), **(validator_params | {"stake": 10 * i})) for i in range(1, 33)]

    config.VALIDATORS_LIMIT = 4
    config.OUR_VALIDATOR_SS58_ADDRESS = "25"

    with monkeypatch.context() as mp:
        mp.setattr(bittensor, "metagraph", MockedMetagraph)
        sync_metagraph()

    validators = Validator.objects.order_by("ss58_address").values_list("ss58_address")
    assert list(validators) == [
        tuple(d.values())
        for d in [
            dict(ss58_address="25"),
            dict(ss58_address="30"),
            dict(ss58_address="31"),
            dict(ss58_address="32"),
        ]
    ]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test__websocket__disconnect_validator_if_become_inactive(
    monkeypatch,
    communicator,
    authenticated,
    validator,
    job,
    dummy_job_params,
):
    """Check that validator is disconnected if it becomes inactive"""
    import bittensor

    await communicator.receive_json_from()
    assert await Channel.objects.filter(validator=validator).aexists()

    class MockedMetagraph:
        def __init__(self, *args, **kwargs):
            self.neurons = [
                MockedNeuron(hotkey=validator.ss58_address, **miner_params),
            ]

    with monkeypatch.context() as mp:
        mp.setattr(bittensor, "metagraph", MockedMetagraph)
        await sync_to_async(sync_metagraph)()

    assert not await Channel.objects.filter(validator=validator).aexists()
    assert (await communicator.receive_output())["type"] == "websocket.close"


def fetch_receipts_test_helper(monkeypatch, mocked_responses, raw_receipt_payloads):
    payload_fields = set()
    for payload_cls in [JobStartedReceiptPayload, JobFinishedReceiptPayload]:
        payload_fields |= set(payload_cls.model_fields.keys())

    buf = io.StringIO()
    csv_writer = csv.DictWriter(
        buf,
        [
            "type",
            "validator_signature",
            "miner_signature",
            *payload_fields,
        ],
    )
    csv_writer.writeheader()
    for raw_receipt in raw_receipt_payloads:
        receipt = json.loads(raw_receipt)
        payload = receipt["payload"]
        if "time_accepted" in payload:
            receipt_type = "JobStartedReceipt"
        elif "time_started" in payload:
            receipt_type = "JobFinishedReceipt"
        else:
            continue
        row = {
            "type": receipt_type,
            "validator_signature": receipt["validator_signature"],
            "miner_signature": receipt["miner_signature"],
        } | payload
        csv_writer.writerow(row)

    mocked_responses.get(
        "http://127.0.0.1:8000/receipts/receipts.csv", status=404
    )  # this one should be gracefully ignored
    mocked_responses.get("http://127.0.0.2:8000/receipts/receipts.csv", body=buf.getvalue())

    class MockedMetagraph:
        def __init__(self, *args, **kwargs):
            self.neurons = [
                MockedNeuron(
                    hotkey="non-serving",
                    axon_info=MockedAxonInfo(is_serving=True, ip="127.0.0.1", port=8000),
                    stake=0.0,
                ),
                MockedNeuron(
                    hotkey=MINER_HOTKEY,
                    axon_info=MockedAxonInfo(is_serving=True, ip="127.0.0.2", port=8000),
                    stake=0.0,
                ),
            ]

    with monkeypatch.context() as mp:
        import bittensor

        mp.setattr(bittensor, "metagraph", MockedMetagraph)
        fetch_receipts()


@pytest.mark.django_db(transaction=True)
def test__fetch_receipts__happy_path(monkeypatch, mocked_responses):
    fetch_receipts_test_helper(monkeypatch, mocked_responses, [RAW_RECEIPT_PAYLOAD_1, RAW_RECEIPT_PAYLOAD_2])

    assert JobStartedReceipt.objects.all().count() == 1
    assert JobFinishedReceipt.objects.all().count() == 1

    receipt_payload = json.loads(RAW_RECEIPT_PAYLOAD_1)
    instance = JobStartedReceipt.objects.get(job_uuid=receipt_payload["payload"]["job_uuid"])
    assert instance.miner_hotkey == receipt_payload["payload"]["miner_hotkey"]
    assert instance.validator_hotkey == receipt_payload["payload"]["validator_hotkey"]
    assert instance.executor_class == receipt_payload["payload"]["executor_class"]
    assert instance.time_accepted == datetime.fromisoformat(receipt_payload["payload"]["time_accepted"])
    assert instance.max_timeout == receipt_payload["payload"]["max_timeout"]

    receipt_payload = json.loads(RAW_RECEIPT_PAYLOAD_2)
    instance = JobFinishedReceipt.objects.get(job_uuid=receipt_payload["payload"]["job_uuid"])
    assert instance.miner_hotkey == receipt_payload["payload"]["miner_hotkey"]
    assert instance.validator_hotkey == receipt_payload["payload"]["validator_hotkey"]
    assert instance.time_started == datetime.fromisoformat(receipt_payload["payload"]["time_started"])
    assert instance.time_took_us == receipt_payload["payload"]["time_took_us"]
    assert instance.score_str == receipt_payload["payload"]["score_str"]


@pytest.mark.django_db(transaction=True)
def test__fetch_receipts__invalid_receipt_skipped(monkeypatch, mocked_responses):
    invalid_receipt_payload = json.dumps({"payload": {"job_uuid": "invalid"}})
    fetch_receipts_test_helper(monkeypatch, mocked_responses, [invalid_receipt_payload, RAW_RECEIPT_PAYLOAD_2])

    # only the valid receipt should be stored
    assert JobStartedReceipt.objects.all().count() == 0
    assert JobFinishedReceipt.objects.all().count() == 1
    assert str(JobFinishedReceipt.objects.get().job_uuid) == json.loads(RAW_RECEIPT_PAYLOAD_2)["payload"]["job_uuid"]


@pytest.mark.django_db(transaction=True)
def test__fetch_receipts__miner_hotkey_mismatch_skipped(monkeypatch, mocked_responses):
    invalid_receipt_payload = RAW_RECEIPT_PAYLOAD_2.replace(
        MINER_HOTKEY,
        MINER_HOTKEY[:-4] + "AAAA",
    )
    fetch_receipts_test_helper(monkeypatch, mocked_responses, [RAW_RECEIPT_PAYLOAD_1, invalid_receipt_payload])

    # only the valid receipt should be stored
    assert JobStartedReceipt.objects.all().count() == 1
    assert JobFinishedReceipt.objects.all().count() == 0
    assert str(JobStartedReceipt.objects.get().job_uuid) == json.loads(RAW_RECEIPT_PAYLOAD_1)["payload"]["job_uuid"]


@pytest.mark.django_db(transaction=True)
def test__fetch_receipts__invalid_miner_signature_skipped(monkeypatch, mocked_responses):
    invalid_char = "0" if PAYLOAD_2_MINER_SIGNATURE[-1] != "0" else "1"
    invalid_receipt_payload = RAW_RECEIPT_PAYLOAD_2.replace(
        PAYLOAD_2_MINER_SIGNATURE,
        PAYLOAD_2_MINER_SIGNATURE[:-1] + invalid_char,
    )
    fetch_receipts_test_helper(monkeypatch, mocked_responses, [RAW_RECEIPT_PAYLOAD_1, invalid_receipt_payload])

    # only the valid receipt should be stored
    assert JobStartedReceipt.objects.all().count() == 1
    assert JobFinishedReceipt.objects.all().count() == 0
    assert str(JobStartedReceipt.objects.get().job_uuid) == json.loads(RAW_RECEIPT_PAYLOAD_1)["payload"]["job_uuid"]


@pytest.mark.django_db(transaction=True)
def test__fetch_receipts__invalid_validator_signature_skipped(monkeypatch, mocked_responses):
    invalid_char = "0" if PAYLOAD_2_VALIDATOR_SIGNATURE[-1] != "0" else "1"
    invalid_receipt_payload = RAW_RECEIPT_PAYLOAD_2.replace(
        PAYLOAD_2_VALIDATOR_SIGNATURE,
        PAYLOAD_2_VALIDATOR_SIGNATURE[:-1] + invalid_char,
    )
    fetch_receipts_test_helper(monkeypatch, mocked_responses, [RAW_RECEIPT_PAYLOAD_1, invalid_receipt_payload])

    # only the valid receipt should be stored
    assert JobStartedReceipt.objects.all().count() == 1
    assert JobFinishedReceipt.objects.all().count() == 0
    assert str(JobStartedReceipt.objects.get().job_uuid) == json.loads(RAW_RECEIPT_PAYLOAD_1)["payload"]["job_uuid"]
