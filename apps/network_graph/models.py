import uuid
from typing import Any

from django.db import models


class NodeType(models.TextChoices):
    PERSON = "PERSON", "Person"
    COMPANY = "COMPANY", "Company"
    MEETING = "MEETING", "Meeting"


class NodeTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    node_type = models.CharField(max_length=10, choices=NodeType.choices, unique=True)
    default_properties = models.JSONField(default=dict, blank=True)
    default_notes = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Template: {self.node_type}"


# System-locked property keys per node type (always present, cannot be deleted)
SYSTEM_LOCKED_DEFAULTS: dict[str, dict[str, str]] = {
    "PERSON": {"First Name": "", "Last Name": "", "Email": "", "Phone Number": ""},
    "COMPANY": {"Company Name": "", "Website": "", "Phone Number": ""},
    "MEETING": {"Date": "", "Attendees": ""},
}


class Node(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    node_type = models.CharField(max_length=10, choices=NodeType.choices, default=NodeType.PERSON)
    properties = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")
    is_ghost = models.BooleanField(default=False)
    profile_image = models.ImageField(upload_to="node_images/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.node_type})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._state.adding and isinstance(self.properties, dict):
            # 1. Apply system locked defaults (setdefault preserves user-supplied values)
            locked = SYSTEM_LOCKED_DEFAULTS.get(self.node_type, {})
            for key, default_val in locked.items():
                self.properties.setdefault(key, default_val)

            # 2. Smart title extraction
            if self.node_type == NodeType.PERSON:
                words = self.title.strip().split()
                if words:
                    if not self.properties.get("First Name"):
                        self.properties["First Name"] = words[0]
                    if not self.properties.get("Last Name") and len(words) >= 2:
                        self.properties["Last Name"] = " ".join(words[1:])
            elif self.node_type == NodeType.COMPANY:
                if not self.properties.get("Company Name"):
                    self.properties["Company Name"] = self.title

            # 3. Merge custom template properties
            template = NodeTemplate.objects.filter(node_type=self.node_type).first()
            if template:
                for key, val in template.default_properties.items():
                    self.properties.setdefault(key, val)
                if not self.notes:
                    self.notes = template.default_notes
            else:
                # 4. Fallback markdown if no template and notes empty
                if not self.notes:
                    if self.node_type == NodeType.MEETING:
                        self.notes = "### Agenda\n-\n\n### Action Items\n- [ ] "
                    elif self.node_type == NodeType.PERSON:
                        self.notes = "### Context\n"

        super().save(*args, **kwargs)


class Connection(models.Model):
    source = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="outgoing")
    target = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="incoming")
    relationship_label = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("source", "target", "relationship_label")]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        label = f" ({self.relationship_label})" if self.relationship_label else ""
        return f"{self.source.title} → {self.target.title}{label}"
