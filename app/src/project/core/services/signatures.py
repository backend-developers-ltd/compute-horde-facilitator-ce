import json

from compute_horde_facilitator_sdk._internal.signature import SignatureInvalidException
from compute_horde_facilitator_sdk.v1 import VERIFIERS_REGISTRY, Signature, signature_from_headers
from django.http import HttpRequest

from project.core.models import SignatureInfo


def signature_info_from_signature(signature: Signature, payload) -> SignatureInfo:
    return SignatureInfo(
        signature_type=signature.signature_type,
        signatory=signature.signatory,
        timestamp_ns=signature.timestamp_ns,
        signature=signature.signature,
        signed_payload=payload,
    )


def signature_info_from_request(request: HttpRequest) -> SignatureInfo:
    """
    Extracts the signature from the request and verifies it.

    :param request: HttpRequest object
    :return: SignatureInfo from the request
    :raises SignatureNotFound: if the signature is not found in the request
    :raises SignatureInvalidException: if the signature is invalid
    """
    signature = signature_from_headers(request.headers)
    try:
        verifier = VERIFIERS_REGISTRY.get(signature.signature_type)
    except KeyError:
        raise SignatureInvalidException(f"Invalid signature type: {signature.signature_type}")
    try:
        json_body = json.loads(request.body)
    except ValueError:
        json_body = None
    payload = verifier.payload_from_request(
        request.method, request.build_absolute_uri(), headers=dict(request.headers), json=json_body
    )
    verifier.verify(payload, signature)
    signature_info = signature_info_from_signature(signature, payload=payload)
    return signature_info
