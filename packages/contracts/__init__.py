"""Cross-service contracts for the training vault."""

from .identity import CallerIdentity
from .query import Citation, QueryAttachment, QueryRequest, QueryResponse
from .sync import SourceFileEvent, SyncExecutionResult, SyncJobAccepted, SyncJobMessage

__all__ = [
    "CallerIdentity",
    "Citation",
    "QueryAttachment",
    "QueryRequest",
    "QueryResponse",
    "SourceFileEvent",
    "SyncExecutionResult",
    "SyncJobAccepted",
    "SyncJobMessage",
]
