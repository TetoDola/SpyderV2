"""One-time migration: convert ATTENDED edges to pairwise KNOWS edges.

For each MEETING node, finds all people who ATTENDED it, then creates
KNOWS edges between every pair of attendees with meeting metadata.

Existing ATTENDED edges and MEETING nodes are left in place.
"""

from __future__ import annotations

from itertools import combinations

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.network_graph.models import Connection, Node


class Command(BaseCommand):
    help = "Convert ATTENDED edges into pairwise KNOWS edges with meeting metadata."

    def handle(self, *args: object, **kwargs: object) -> None:
        meetings = Node.objects.filter(node_type="MEETING")
        total_meetings = meetings.count()
        created_count = 0
        updated_count = 0

        self.stdout.write(f"Found {total_meetings} MEETING nodes to process.")

        for i, meeting in enumerate(meetings, 1):
            # Find all people who ATTENDED this meeting
            attended = Connection.objects.filter(
                target=meeting,
                relationship_label="ATTENDED",
            ).select_related("source")

            attendee_nodes = [conn.source for conn in attended if conn.source.node_type == "PERSON"]

            if len(attendee_nodes) < 2:
                continue

            meeting_id = str(meeting.pk)
            meeting_title = meeting.title or "Untitled meeting"
            props = meeting.properties if isinstance(meeting.properties, dict) else {}
            meeting_date = str(props.get("Date", ""))

            meeting_entry = {
                "meeting_node_id": meeting_id,
                "title": meeting_title,
                "date": meeting_date,
                "context": "",
            }

            for person_a, person_b in combinations(attendee_nodes, 2):
                id_a = str(person_a.pk)
                id_b = str(person_b.pk)

                # Check for existing KNOWS edge in either direction
                existing = Connection.objects.filter(
                    Q(source_id=id_a, target_id=id_b) | Q(source_id=id_b, target_id=id_a),
                    relationship_label="KNOWS",
                ).first()

                if existing:
                    # Append meeting if not already present
                    meta = existing.metadata if isinstance(existing.metadata, dict) else {}
                    meetings_list: list[dict[str, str]] = meta.get("meetings", [])

                    if any(m.get("meeting_node_id") == meeting_id for m in meetings_list):
                        continue

                    meetings_list.append(meeting_entry)
                    meta["meetings"] = meetings_list
                    meta["interaction_count"] = len(meetings_list)
                    meta["last_interaction"] = meeting_date
                    if not meta.get("first_met"):
                        meta["first_met"] = meeting_date

                    existing.metadata = meta
                    existing.save(update_fields=["metadata"])
                    updated_count += 1
                else:
                    Connection.objects.create(
                        source_id=id_a,
                        target_id=id_b,
                        relationship_label="KNOWS",
                        metadata={
                            "meetings": [meeting_entry],
                            "first_met": meeting_date,
                            "interaction_count": 1,
                            "last_interaction": meeting_date,
                        },
                    )
                    created_count += 1

            if i % 50 == 0:
                self.stdout.write(f"  Processed {i}/{total_meetings} meetings...")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {created_count} KNOWS edges, updated {updated_count} existing."
            )
        )
