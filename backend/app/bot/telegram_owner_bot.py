from app.agents import BottleneckAgent, FounderReportAgent, MemoryQAAgent, SOPAgent


COMMANDS = {
    "/ask": "Ask the company brain a question.",
    "/report today": "Generate today's founder report.",
    "/report week": "Generate this week's founder report.",
    "/bottlenecks week": "Analyze recurring bottlenecks.",
    "/decisions week": "Search decision facts.",
    "/complaints week": "Search complaint facts.",
    "/tasks open": "Search task facts.",
    "/sop sales": "Draft a sales SOP.",
}


class TelegramOwnerBot:
    """Skeleton for an owner/manager bot. It never auto-replies to customers in v1."""

    def __init__(self):
        self.qa_agent = MemoryQAAgent()
        self.report_agent = FounderReportAgent()
        self.bottleneck_agent = BottleneckAgent()
        self.sop_agent = SOPAgent()

