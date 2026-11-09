"""Direct messages between agents, for the agent-to-agent pattern.

Each agent has an inbox. A message goes into the recipient's inbox and is saved on the
blackboard, so the whole conversation can be read back afterwards. The number of messages per
run is capped: a message can wake an idle agent, which costs a model call.
"""

from dataclasses import dataclass, field

AGENTS = ["orchestrator", "observability", "dependency", "reasoning", "critic"]
SPECIALISTS = ["observability", "dependency"]


@dataclass
class Delivered:
    sender: str
    intent: str
    text: str
    evidence_ids: list = field(default_factory=list)


class Conversation:
    def __init__(self, board, max_messages=6, max_specialist_messages=3):
        self.board = board
        self.max_messages = max_messages
        # Specialists cannot use up the whole budget: Reasoning and the Critic need some.
        self.max_specialist_messages = max_specialist_messages
        self.sent = 0
        self.specialist_sent = 0
        self.replies = 0
        self.dropped = 0
        self.unanswered = []      # [sender, recipient] pairs still waiting for a reply
        self.inboxes = {}
        for agent in AGENTS:
            self.inboxes[agent] = []

    def post(self, sender, messages, round_number):
        for message in messages:
            if message.to == sender:
                continue
            if [message.to, sender] in self.unanswered:
                # A reply to an open question always gets through and costs no budget. It
                # closes the question and opens none, so replies can never outnumber questions.
                self.unanswered.remove([message.to, sender])
                self.replies += 1
            elif self.refused(sender, message.to):
                self.dropped += 1
                continue
            else:
                self.sent += 1
                if sender in SPECIALISTS:
                    self.specialist_sent += 1
                self.unanswered.append([sender, message.to])
            self.inboxes[message.to].append(Delivered(sender, message.intent, message.text,
                                                      list(message.evidence_ids)))
            self.board.add_message(sender, message.to, message.intent, message.text,
                                   list(message.evidence_ids), round_number)

    def refused(self, sender, recipient):
        if self.sent >= self.max_messages:
            return True
        if sender in SPECIALISTS and self.specialist_sent >= self.max_specialist_messages:
            return True
        # One open question per pair: no asking again before the colleague has replied.
        return [sender, recipient] in self.unanswered

    def log(self, sender, recipient, kind, text, round_number):
        """Record the work around the messages, for the history. Not a message: wakes nobody."""
        self.board.add_message(sender, recipient, kind, text, [], round_number)

    def has_mail(self, agent):
        return len(self.inboxes[agent]) > 0

    def take(self, agent):
        """Everything waiting for this agent. Each message is delivered once."""
        delivered = self.inboxes[agent]
        self.inboxes[agent] = []
        return delivered
