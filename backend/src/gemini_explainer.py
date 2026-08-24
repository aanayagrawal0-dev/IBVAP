"""
Gemini-powered plain-language explanations for history events.

Deliberately isolated behind one function (explain_event) so the rest of
the backend never touches the Gemini SDK directly. Everything here fails
with a specific, catchable exception rather than a bare crash, so the API
endpoint that calls this can turn "no key configured" / "bad key" /
"network error" into a clear message on the History page instead of a
generic 500.

Nothing here is called automatically — an explanation is only generated
the moment an operator expands a row in the History page and none is
cached yet (see api_server.py's /api/history/{id}/explain and
history_store.py's explanation column). That keeps this on-demand rather
than something that silently spends API quota on every zone-crossing
event the CV pipeline logs.
"""

import os

# gemini-2.5-flash was retired for new API keys shortly after this was
# first written (Google's own 404 points new callers at 3.6-flash instead)
# — a reminder that Google's model lineup moves fast. Override via
# GEMINI_MODEL without touching code if this default goes stale again.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

_PROMPT_TEMPLATE = """You are an assistant embedded in IBVAP, an AI border-security video \
analytics system. Below is one automatically detected event from a computer-vision pipeline \
(YOLOv8 detection + ByteTrack tracking + polygon zone-crossing geometry) — not a human-written \
report, so treat the numbers as sensor output, not ground truth.

Event details:
- Camera: {camera_id}
- What fired: {title}
- Zone: {zone_name}
- Tracked object: {class_name} (tracker #{tracker_id})
- Severity assigned by the system: {severity}
- Timestamp: {timestamp}

{image_note}

In 2-4 short sentences, explain in plain language what this event means operationally, what's \
visible in the frame if one is attached, and whether the assigned severity looks reasonable \
given what you can see. Write for a border-security operator glancing at a dashboard — be \
concrete and specific, not generic boilerplate. Plain prose only, no markdown formatting, no \
headers, no bullet points."""


class GeminiNotConfigured(RuntimeError):
    """No GEMINI_API_KEY set on the backend."""


class GeminiRequestFailed(RuntimeError):
    """The SDK call itself failed — bad key, quota, network, empty response, etc."""


def _hint_for(exc: Exception) -> str:
    """Appends an actionable one-liner for the specific failures this has
    actually been seen to produce, since Google's raw error text alone
    (e.g. a generic "Expected OAuth 2 access token...") doesn't point at
    the fix. Anything not recognized here is returned unmodified — this
    only ever adds context, never hides the original message."""
    text = str(exc)
    if "ACCESS_TOKEN_TYPE_UNSUPPORTED" in text or "UNAUTHENTICATED" in text:
        return (
            " — this means the request reached Google but wasn't accepted as a valid API "
            "key. Double-check GEMINI_API_KEY is a plain Gemini Developer API key from "
            "https://aistudio.google.com/apikey (starts with 'AIza...'), not a Google Cloud "
            "OAuth client ID/secret or a service-account credential — those need a completely "
            "different auth setup than this project uses. Also check for stray quotes/spaces "
            "around the key value where you set it, and that GEMINI_API_KEY is exported (or in "
            ".env) in the same terminal you started the backend from."
        )
    if "PERMISSION_DENIED" in text or "403" in text:
        return (
            " — the key was recognized but rejected for this request. Check that the "
            "Generative Language API is enabled for whichever Google Cloud project the key "
            "belongs to, and that the key has no IP/referrer restrictions blocking a server-"
            "side request."
        )
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        return " — you've hit a rate limit or quota cap on this key; wait a bit and retry."
    return ""


def _client():
    from google import genai  # imported lazily — see api_server.py's endpoint for why

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise GeminiNotConfigured(
            "GEMINI_API_KEY is not set on the backend. Export it and restart the server."
        )
    # vertexai=False is explicit on purpose: the SDK otherwise auto-detects
    # Vertex AI mode from ambient env vars (GOOGLE_GENAI_USE_VERTEXAI,
    # GOOGLE_CLOUD_PROJECT, etc.) — if any of those happen to be set on the
    # machine (e.g. left over from an unrelated gcloud/Vertex AI setup),
    # the SDK silently switches to OAuth/service-account auth and ignores
    # api_key entirely, which surfaces as a confusing 401 UNAUTHENTICATED
    # ("Expected OAuth 2 access token...") instead of using the key we
    # just validated above. Pinning this to the plain Gemini Developer API
    # removes that whole class of environment-dependent failure.
    return genai.Client(api_key=api_key, vertexai=False)


def explain_event(event: dict, thumbnail_jpeg: bytes | None) -> str:
    """event is a row dict from history_store.query_events()/get_event() — needs at least
    camera_id, title, zone_name, class_name, tracker_id, severity, ts_iso. Returns Gemini's
    plain-text explanation, or raises GeminiNotConfigured / GeminiRequestFailed."""
    from google.genai import types  # lazy, same reason as _client()

    client = _client()

    prompt = _PROMPT_TEMPLATE.format(
        camera_id=event["camera_id"],
        title=event["title"],
        zone_name=event["zone_name"],
        class_name=event["class_name"],
        tracker_id=event["tracker_id"],
        severity=event["severity"],
        timestamp=event["ts_iso"],
        image_note=(
            "A snapshot frame from the moment of the event is attached."
            if thumbnail_jpeg
            else "No snapshot frame is available for this event — reason from the metadata alone."
        ),
    )

    contents = [prompt]
    if thumbnail_jpeg:
        contents.append(types.Part.from_bytes(data=thumbnail_jpeg, mime_type="image/jpeg"))

    try:
        response = client.models.generate_content(model=DEFAULT_MODEL, contents=contents)
    except Exception as exc:  # the SDK raises several different error types — normalize all of them
        raise GeminiRequestFailed(f"{exc}{_hint_for(exc)}") from exc

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise GeminiRequestFailed("Gemini returned an empty response.")
    return text
