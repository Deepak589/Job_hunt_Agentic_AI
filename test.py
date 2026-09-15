import dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser


dotenv.load_dotenv()


prompt_template = ChatPromptTemplate.from_template("explain about {topic}")
model = ChatAnthropic(model_name="claude-haiku-4-5-20251001")
parser = StrOutputParser()

chain = prompt_template | model | parser
result = chain.invoke({"topic": "RAG"})
print(result)


