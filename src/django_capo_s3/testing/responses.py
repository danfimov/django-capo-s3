from collections.abc import Callable, Mapping
from typing import TypeVar

from capo_s3._protocol.xml import Element, SubElement, tostring
from zapros import Response

T = TypeVar("T")

_XML_HEADERS = {"Content-Type": "application/xml"}


def xml_response(
    serialize: Callable[[T, Element, str], None],
    value: T,
    tag: str,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """Build a response from one of capo's own output serializers, so the XML matches what the client parses.

    Pass the operation's serialize_xml, the output dict, and the root tag S3 uses for it — for instance
    capo_s3.types.list_objects_v2_output.serialize_xml with "ListBucketResult". Reach for this when a test
    needs a reply the fake service won't produce on its own, such as a truncated listing with a chosen token.
    """
    root = Element("root")
    serialize(value, root, tag)
    return Response(status=status, headers={**_XML_HEADERS, **(headers or {})}, content=tostring(root[0]))


def s3_error(
    code: str,
    *,
    status: int = 400,
    message: str | None = None,
    resource: str | None = None,
    request_id: str = "test-request-id",
) -> Response:
    """Build an S3 error response, the way the service reports a failed operation.

    The code is what the client turns into an exception: "NoSuchKey" and "NotFound" become the matching
    capo errors, anything it doesn't model becomes UnknownServiceError.
    """
    error = Element("Error")
    SubElement(error, "Code").text = code
    SubElement(error, "Message").text = message if message is not None else code
    if resource is not None:
        SubElement(error, "Resource").text = resource
    SubElement(error, "RequestId").text = request_id
    return Response(status=status, headers=_XML_HEADERS, content=tostring(error))


def not_found() -> Response:
    """Build the bodyless 404 that answers a HEAD request for a missing object.

    HEAD carries no response body, so the client has only the status to go on; use this instead of s3_error
    when the operation under test is HeadObject or HeadBucket.
    """
    return Response(status=404)
