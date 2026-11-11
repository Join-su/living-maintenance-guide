"""EV-069 CHG-EF-1 (D-103 · D-433, EV-030 CHG-160): a Pack version's fixed flow — the promises an efficiency candidate must keep.

packrt reads the list from the version's Pack folder (`packrt.ir.fixed_flow`, from packrt 0.12.6; pinned 0.15.0 (0.13.2: a custom rule's signature the same on every Python; 0.15.0: a function node's declared options), whose check item is
`checks:<graph>`: every way to the output keeps a check as strict as the original last one) and checks a candidate against it
(`packrt.ir.check_fixed_flow`); LLMOps keeps one row per release in `fixed_flow` (items as read · who confirmed) and never edits
the list (D-103). A person confirms it once per revision; a version the efficiency wheel made inherits its base's confirmed list
(packrt 124 Q-1 — the maker cannot change the list). An accuracy-wheel version or a re-read version is read again."""
import packrt.ir as ir

STATE_NEED, STATE_DONE, STATE_UNREADABLE = "확인 필요", "확인함", "읽지 못함"


def _folder(rel):
    pk = (rel or {}).get("pack") or {}
    return pk.get("copy") or pk.get("work_folder")


def read(rel):
    """{'items', 'errors'} as packrt reads the release's Pack folder; an error entry when packrt cannot (no function · no folder)."""
    fn = getattr(ir, "fixed_flow", None)
    if fn is None:
        return {"items": [], "errors": [{"message": "packrt에 fixed_flow 없음 — packrt 0.12.6 필요"}]}
    folder = _folder(rel)
    if not folder:
        return {"items": [], "errors": [{"message": "Pack 사본 폴더 없음"}]}
    try:
        out = fn(folder)
    except Exception as e:   # noqa: BLE001 - packrt refused the folder: the row says why, the start is refused
        return {"items": [], "errors": [{"message": f"{type(e).__name__}: {str(e)[:200]}"}]}
    return {"items": list(out.get("items") or []), "errors": list(out.get("errors") or []), "packrt": packrt_version()}


def packrt_version():
    import packrt
    return getattr(packrt, "__version__", None)


def ensure(store, rel, before=()):
    """The release's row for its current revision (see _read_row). D-613: before = the versions just before it that differ only in the
    graph, nearest first — an unconfirmed row takes over the nearest confirmation of the same list; the row says from where and whose."""
    row = _read_row(store, rel)
    if row["items"].get("errors") or row.get("confirmed_by"):
        return row
    for b in before:
        got = store.fixed_flow(b["id"])
        if not got or not got.get("confirmed_by") or got["items"].get("errors"):
            continue
        if got["items"].get("items") != row["items"].get("items"):
            return row   # another list: a person looks at it again
        who = got["items"].get("confirmed_person") or got["confirmed_by"]
        return store.set_fixed_flow(rel["id"], row["pack_revision"], {**row["items"], "inherited_from": b["id"], "confirmed_person": who},
                                    confirmed_by=f"이어받음({b['name']} · {who})")
    return row


def _read_row(store, rel):
    """The release's row for its current revision — read now when it has none, it is of another revision, or another packrt read it
    (D-466 · packrt 0.13.0 changes what an item means; a row from before the stamp is read once more). Read again to the same list,
    the person's confirmation (or the inherited one) stays; to another list it needs a new confirmation (on demand)."""
    revision = ((rel or {}).get("pack") or {}).get("revision")
    row = store.fixed_flow(rel["id"])
    same = row and row["pack_revision"] == revision
    if same and not row["items"].get("errors") and row["items"].get("packrt") == packrt_version():   # an unreadable row is read again (slice review 1 B2)
        return row
    got = read(rel)
    if same and row.get("confirmed_by") and not row["items"].get("errors") and not got["errors"] and got["items"] == row["items"].get("items"):
        return store.set_fixed_flow(rel["id"], revision, {**row["items"], **got}, confirmed_by=row["confirmed_by"])
    return store.set_fixed_flow(rel["id"], revision, got)


def inherit(store, new_rel, base_rel):
    """The efficiency wheel's new version keeps its base's confirmed list as it is (CHG-EF-1, packrt 124 Q-1)."""
    base = store.fixed_flow(base_rel["id"])
    if not base or not base.get("confirmed_by"):
        return ensure(store, new_rel)
    return store.set_fixed_flow(new_rel["id"], new_rel["pack"]["revision"], {**base["items"], "inherited_from": base_rel["id"]},
                                confirmed_by=f"이어받음({base_rel['name']})")


def state(row):
    if not row or row["items"].get("errors"):
        return STATE_UNREADABLE
    return STATE_DONE if row.get("confirmed_by") else STATE_NEED


def shown(row, revision):
    """D-485: the version band's word from the stored row alone (no packrt read): a row of another revision, or none, is '확인 필요'
    (the window reads it on demand)."""
    if not row or row.get("pack_revision") != revision:
        return STATE_NEED
    return state(row)


def view(row):
    errs = (row or {}).get("items", {}).get("errors") or []
    return {"state": state(row), "items": (row or {}).get("items", {}).get("items") or [], "why": errs[0].get("message") if errs else None,
            "revision": (row or {}).get("pack_revision"), "confirmed_by": (row or {}).get("confirmed_by"), "confirmed_at": (row or {}).get("confirmed_at"),
            "inherited_from": (row or {}).get("items", {}).get("inherited_from"), "confirmed_person": (row or {}).get("items", {}).get("confirmed_person")}


def check(items, folder):
    """packrt's violations of the confirmed list for a candidate Pack folder ([] = all kept)."""
    return list(ir.check_fixed_flow(items, folder))
