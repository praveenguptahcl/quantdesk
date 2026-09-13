"""nqq.rooms — collaboration rooms: a shared space to discuss ideas.

Lightweight shared workspaces where a team pins strategies and comments. Every post
is stamped to the append-only ledger, so a room's history is auditable. Ported from
zingq rooms / alphaforge collaboration.
"""
import time


def create(store, owner, name):
    if not name:
        return None, "room name required"
    rid = int(time.time() * 1000)
    doc = {"id": rid, "name": name, "owner": owner, "members": [owner],
           "posts": [], "pinned": [], "created": time.strftime("%Y-%m-%d %H:%M")}
    store.put("rooms", rid, doc)
    store.feed("info", f"ROOMS — '{name}' created by {owner}")
    return doc, None


def join(store, room_id, user):
    """Explicitly join a room (open collaboration, but membership is intentional — posting
    no longer silently auto-joins you, which used to expose non-members' posts)."""
    def _add(room):
        if not room:
            return None
        if user not in room["members"]:
            room["members"].append(user)
        return room
    room = store.mutate("rooms", room_id, _add)
    if room is None:
        return None, "room not found"
    return room, None


def post(store, room_id, author, text, pin_idea=None):
    room = store.get("rooms", room_id)
    if not room:
        return None, "room not found"
    # tenancy: must be a member to post — previously a non-member was silently auto-joined,
    # so the members list was decorative and anyone could inject into any room
    if author not in room["members"] and author != "admin":
        return None, "join the room before posting"

    def _apply(r):
        r["posts"].insert(0, {"author": author, "text": text[:500],
                              "at": time.strftime("%H:%M")})
        r["posts"] = r["posts"][:100]
        if pin_idea and pin_idea not in r["pinned"]:
            r["pinned"].append(pin_idea)
        return r
    room = store.mutate("rooms", room_id, _apply)
    store.stamp("room_post", str(room_id), {"by": author, "text": text[:80]})
    return room, None


def listing(store, viewer=None, is_admin=False):
    """Rooms visible to the viewer. In multiuser mode a non-admin sees only rooms they
    own or are a member of — full post bodies were previously readable by ANY user."""
    rooms = sorted(store.list("rooms"), key=lambda r: r["id"], reverse=True)
    if viewer is not None and not is_admin:
        vis = [r for r in rooms if viewer == r.get("owner") or viewer in (r.get("members") or [])]
        others = [{"id": r["id"], "name": r["name"], "owner": r.get("owner"),
                   "members": len(r.get("members") or []), "joinable": True,
                   "posts": [], "pinned": []}
                  for r in rooms if r not in vis]   # discoverable (name only), not readable
        return {"rooms": vis, "discoverable": others}
    return {"rooms": rooms, "discoverable": []}


def seed(store, force=False):
    if store.get_kv("rooms:seeded") == "1" and not force:
        return {"seeded": False}
    r, _ = create(store, "admin", "Momentum working group")
    if r:
        post(store, r["id"], "alice", "Momentum's worst regime keeps failing the gate — "
             "anyone tried a shorter lookback with a vol filter?")
        post(store, r["id"], "bob", "Discovery rejected all my RSI variants at FDR. "
             "Honest, but humbling.")
    store.set_kv("rooms:seeded", "1")
    return {"seeded": True}
