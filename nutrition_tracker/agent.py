from google.adk.agents import SequentialAgent

from .intent import intent_agent
from .nutrition import nutrition_agent

root_agent = SequentialAgent(
    name="CalorieTrackerPipeline",
    sub_agents=[intent_agent, nutrition_agent]
)