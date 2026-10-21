"""One module per graph node. Nodes are async (LangGraph only enforces timeouts on async nodes)."""

from fielddesk.schemas import TicketInput


def ticket_block(ticket: TicketInput) -> str:
    """Render a ticket for a prompt, fenced so the model can tell data from instructions.

    A literal closing tag inside dealer text is defanged so it cannot end the block early.
    """

    def safe(value: object) -> str:
        return str(value).replace("</ticket>", "</ ticket>").replace("<ticket>", "< ticket>")

    hours = "not provided" if ticket.machine_hours is None else str(ticket.machine_hours)
    lines = [
        f"dealer_name: {safe(ticket.dealer_name)}",
        f"machine_model: {safe(ticket.machine_model)}",
        f"error_code: {safe(ticket.error_code or 'not provided')}",
        f"warranty_status: {ticket.warranty_status.value}",
        f"machine_hours: {hours}",
        f"subject: {safe(ticket.subject)}",
        "message:",
        safe(ticket.message),
    ]
    return "<ticket>\n" + "\n".join(lines) + "\n</ticket>"
