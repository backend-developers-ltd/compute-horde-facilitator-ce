import base64

import pytest
from compute_horde_facilitator_sdk.v1 import (
    VERIFIERS_REGISTRY,
    Signature,
    SignatureInvalidException,
    signature_to_headers,
)
from django.utils.datastructures import CaseInsensitiveMapping

from project.core.services.signatures import signature_info_from_request, signature_info_from_signature


@pytest.fixture
def request_json():
    return {"key": "value"}


@pytest.fixture
def mock_request(rf, request_json):
    request = rf.post(
        "/test-url/",
        data=request_json,
        content_type="application/json",
    )
    return request


@pytest.fixture
def verifier():
    return VERIFIERS_REGISTRY.get("bittensor")


@pytest.fixture
def payload_from_request(verifier, request_json):
    return verifier.payload_from_request(method="POST", url="/test-url/", headers={}, json=request_json)


@pytest.fixture
def signature(keypair, payload_from_request):
    signature = Signature(
        signature_type="bittensor",
        signatory=keypair.ss58_address,
        timestamp_ns=1719427963187622189,
        signature=base64.b85decode("oH9<VaQ`;rRSBpEE@-r|Cb-DNWI*dZrUew5&;-C{Zfi2~bLQwK3(b}S@UiT}rD{8`(T{c>@)8<R-Cgj6"),
    )
    # This signature was generated using the following code:
    # signer = SIGNERS_REGISTRY.get("bittensor", keypair)
    # signature = signer.sign(payload=payload_from_request)
    # but its hardcoded since we want to check for backwards compatibility
    return signature


@pytest.fixture
def mock_request_with_signature(mock_request, signature):
    mock_request.headers = CaseInsensitiveMapping({**mock_request.headers, **signature_to_headers(signature)})
    return mock_request


def test_signature_info_from_signature(signature):
    payload = {"key": "value"}
    signature_info = signature_info_from_signature(signature, payload)

    assert signature_info.signature_type == signature.signature_type
    assert signature_info.signatory == signature.signatory
    assert signature_info.timestamp_ns == signature.timestamp_ns
    assert signature_info.signature == signature.signature
    assert signature_info.signed_payload == payload


def test_signature_info_from_request(mock_request_with_signature, signature, request_json):
    signature_info = signature_info_from_request(mock_request_with_signature)

    assert signature_info.signature_type == signature.signature_type
    assert signature_info.signatory == signature.signatory
    assert signature_info.timestamp_ns == signature.timestamp_ns
    assert signature_info.signature == signature.signature
    assert signature_info.signed_payload == {"action": "POST /test-url/", "json": request_json}


def test_signature_info_from_request__invalid_signature(mock_request_with_signature):
    mock_request_with_signature.headers = CaseInsensitiveMapping(
        {**mock_request_with_signature.headers, **{"X-CH-Signature": "invalid_signature"}}
    )
    with pytest.raises(SignatureInvalidException):
        signature_info_from_request(mock_request_with_signature)
