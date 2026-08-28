from dotenv import load_dotenv
load_dotenv()

import os
from groq import Groq

client = Groq(api_key=os.environ["GROQ_API_KEY"])

for model in client.models.list().data:
    print(model.id)