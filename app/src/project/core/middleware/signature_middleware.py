from compute_horde_facilitator_sdk.v1 import SignatureNotFound
from django.utils.deprecation import MiddlewareMixin

from ..services.signatures import signature_info_from_request


class FacilitatorSignatureMiddleware(MiddlewareMixin):
    """
    Middleware that extracts the signature from the request and saves it to the database
    """

    def process_request(self, request):
        try:
            signature_info = signature_info_from_request(request)
        except SignatureNotFound:
            signature_info = None
        else:
            signature_info.save()

        request.signature_info = signature_info


def require_signature(request):
    if not getattr(request, "signature_info", None):
        raise SignatureNotFound("Request signature not found, but is required")
