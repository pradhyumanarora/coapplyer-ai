# Copy this file to config.py and adjust values for your local environment.

from constants import DEBUG

# Config related to logging must have prefix LOG_
LOG_LEVEL = DEBUG
LOG_SELENIUM_LEVEL = DEBUG
LOG_TO_FILE = True
LOG_TO_CONSOLE = True

MINIMUM_WAIT_TIME_IN_SECONDS = 60

JOB_APPLICATIONS_DIR = "job_applications"
JOB_SUITABILITY_SCORE = 7
DISABLE_DESCRIPTION_FILTER = False

JOB_MAX_APPLICATIONS = 5
JOB_MIN_APPLICATIONS = 1

LLM_MODEL_TYPE = "azure_openai"
LLM_MODEL = "gpt-4o-mini"
# Only required for OLLAMA models
LLM_API_URL = ""

# Azure OpenAI settings (used when LLM_MODEL_TYPE = "azure_openai")
AZURE_OPENAI_ENDPOINT = "https://your-resource.services.ai.azure.com"
AZURE_OPENAI_DEPLOYMENT = "your-deployment-name"
AZURE_OPENAI_API_VERSION = "2024-02-01"
