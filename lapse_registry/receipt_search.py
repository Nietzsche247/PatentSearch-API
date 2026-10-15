"""Hook for checklist 10.3: the snapshot search a receipt runs. 10.3 wires it to the query of /api/v1/lapse/similar
(API/lapse_views.py SimilarView) on one pinned read-only snapshot connection for the whole receipt (checklist 10.12).
Until then it refuses, and the receipt route answers 501 before it is reached (REGISTRY_RECEIPTS_ENABLED is off)."""


def similar(text, n):
    raise NotImplementedError("the receipt search arrives with checklist 10.3")
