import uuid

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
    summary = models.JSONField(default=dict, blank=True)
    is_ghost = models.BooleanField(default=False)
    profile_image = models.ImageField(upload_to="node_images/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.node_type})"

    def save(self, *args: object, **kwargs: object) -> None:
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


# ---------------------------------------------------------------------------
# Pipeline Models
# ---------------------------------------------------------------------------


class IngestionSourceType(models.TextChoices):
    VOICE_NOTE = "VOICE_NOTE", "Voice Note"
    DOCUMENT = "DOCUMENT", "Document"
    FREEFORM_NOTE = "FREEFORM_NOTE", "Freeform Note"
    MEETING = "MEETING", "Meeting"


class IngestionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    TRANSCRIBING = "TRANSCRIBING", "Transcribing"
    EXTRACTING = "EXTRACTING", "Extracting"
    RESOLVING = "RESOLVING", "Resolving"
    WRITING = "WRITING", "Writing"
    SUMMARIZING = "SUMMARIZING", "Summarizing"
    COMPLETE = "COMPLETE", "Complete"
    FAILED = "FAILED", "Failed"
    DISMISSED = "DISMISSED", "Dismissed"


class Ingestion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_type = models.CharField(
        max_length=20,
        choices=IngestionSourceType.choices,
    )
    original_file = models.FileField(upload_to="ingestions/", blank=True, null=True)
    raw_text = models.TextField(blank=True, default="")
    extracted_json = models.JSONField(default=dict, blank=True)
    dsl_commands = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=20,
        choices=IngestionStatus.choices,
        default=IngestionStatus.PENDING,
    )
    title = models.CharField(max_length=255, blank=True, default="")
    auto_create = models.BooleanField(default=True)
    error_message = models.TextField(blank=True, default="")
    failed_step = models.CharField(max_length=20, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Ingestion {self.id} ({self.source_type} / {self.status})"


class ResolutionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    CONFIRMED = "CONFIRMED", "Confirmed"
    REJECTED = "REJECTED", "Rejected"
    AUTO_LINKED = "AUTO_LINKED", "Auto-Linked"


class ResolutionCandidate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ingestion = models.ForeignKey(
        Ingestion,
        on_delete=models.CASCADE,
        related_name="resolution_candidates",
    )
    extracted_name = models.CharField(max_length=255)
    extracted_email = models.CharField(max_length=255, blank=True, default="")
    extracted_company = models.CharField(max_length=255, blank=True, default="")
    extracted_title = models.CharField(max_length=255, blank=True, default="")
    candidate_node = models.ForeignKey(
        Node,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolution_matches",
    )
    confidence = models.FloatField(default=0.0)
    status = models.CharField(
        max_length=20,
        choices=ResolutionStatus.choices,
        default=ResolutionStatus.PENDING,
    )
    resolved_node = models.ForeignKey(
        Node,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_from",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Resolution: {self.extracted_name} ({self.status})"
