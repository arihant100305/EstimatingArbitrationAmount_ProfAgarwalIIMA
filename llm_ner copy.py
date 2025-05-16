import asyncio
import os
import time
import html
import json
from urllib.parse import urlparse
from pathlib import Path
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
from googlesearch import search  
from openai import OpenAI
import re
from bs4 import BeautifulSoup

DEEPSEEK_API_KEY = ""
PHRASE = "Unión Fenosa Gas (UFG) vs Egypt arbitration case" 
TOP_N = 10
output_directory = f"scraped_content/{PHRASE.replace(' ', '_')[:10]}"  

def get_top_urls(query, num_results):
    urls = []
    for url in search(query, num_results=num_results):
        try:
            parsed = urlparse(url)
            if parsed.scheme and parsed.netloc:  
                urls.append(url)
                print(f"Found valid URL: {url}")
            else:
                print(f"Skipping invalid URL: {url}")
        except Exception as e:
            print(f"Error parsing URL {url}: {e}")
        time.sleep(0.1) 
    return urls

def sanitize_filename(url):
    parsed = urlparse(url)
    netloc = parsed.netloc
    if (netloc.startswith('www.')):
        netloc = netloc[4:]  
    filename = netloc + parsed.path.replace('/', '_')
    filename = ''.join(c if c.isalnum() or c in ['_', '-', '.'] else '_' for c in filename)
    return filename[:10] 

def create_label_studio_xml(html_content, url, title):
    json_content = json.dumps([{"html_content": html_content}])
    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<View>
  <HyperText name="text" value="$html_content" valueType="text" />
  <Labels name="labels" toName="text">
    <Label value="Important" />
    <Label value="Review" />
  </Labels>
  <Meta>
    <Info name="url" value="{html.escape(url)}"/>
    <Info name="title" value="{html.escape(title)}"/>
  </Meta>
</View>
"""
    return xml_content, json_content

def extract_text_from_html(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    
    for script_or_style in soup(['script', 'style', 'meta', 'link', 'noscript']):
        script_or_style.decompose()

    text = soup.get_text(separator=' ', strip=True)
    text = re.sub(r'\s+', ' ', text)
    return text

def read_scraped_content(directory_path):
    html_dir = Path(directory_path) / "html"
    all_text = []
    
    if not html_dir.exists():
        return ""
    
    for file_path in html_dir.glob('*.html'):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                html_content = f.read()
                text_content = extract_text_from_html(html_content)
                all_text.append(text_content)
        except Exception as e:
            print(f"Error processing {file_path}: {e}")
    
    return "\n\n".join(all_text)

async def main() -> None:
    output_path = Path(output_directory)
    output_path.mkdir(parents=True, exist_ok=True)

    html_output_path = output_path / "html"
    html_output_path.mkdir(exist_ok=True)
    
    json_output_path = output_path / "json"
    json_output_path.mkdir(exist_ok=True)

    print(f"Searching Google for: {PHRASE}")
    urls = get_top_urls(PHRASE, TOP_N)
    
    if not urls:
        print("No valid URLs were found. Exiting.")
        return
    
    crawler = PlaywrightCrawler(
        headless=True, 
    )

    @crawler.router.default_handler
    async def request_handler(context: PlaywrightCrawlingContext) -> None:
        url = context.request.url
        context.log.info(f'Processing {url} ...')

        await context.page.wait_for_load_state("networkidle", timeout=30000)
        html_content = await context.page.content()
        title = await context.page.title()
        _, json_content = create_label_studio_xml(html_content, url, title)

        filename = sanitize_filename(url)

        json_filename = filename + '.json'
        json_file_path = json_output_path / json_filename
        with open(json_file_path, 'w', encoding='utf-8') as f:
            f.write(json_content)
        context.log.info(f'Saved JSON content to {json_file_path}')

        html_filename = filename + '.html'
        html_file_path = html_output_path / html_filename
        with open(html_file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        context.log.info(f'Saved HTML content to {html_file_path}')

        data = {
            'url': url,
            'title': title,
            'saved_json': str(json_file_path),
            'saved_html': str(html_file_path),
        }
        await context.push_data(data)

    try:
        await crawler.run(urls)
        print(f"Scraping completed. Files saved to:")
        print(f"- JSON: {output_directory}/json/")
        print(f"- HTML: {output_directory}/html/")
        print(f"You can now import the JSON files into Label Studio for annotation.")
    except Exception as e:
        print(f"Error during crawling: {e}")
        print("Continuing with any scraped content...")

    combined_text = read_scraped_content(output_directory)
    if combined_text:
        process_with_deepseek(combined_text, PHRASE)
    else:
        print("No content was scraped or content processing failed.")

def process_with_deepseek(content, search_phrase):

    max_content_length = 100000  
    if len(content) > max_content_length:
        print(f"Content length: {len(content)}")
        print("Content is too long, truncating to 100,000 characters")
        content = content[:max_content_length] + "... (content truncated)"
    
    if not DEEPSEEK_API_KEY:
        print("Error: DeepSeek API key is not set.")
        return
    
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com",
    )

    system_prompt = """
    You are an expert legal analyst specializing in arbitration cases. 
    Given text about arbitration cases, identify the following entities and extract them in JSON format:

    Case Overview:
    - Case Description: Detailed description of the case including background, key issues, and context
    - Case Timeline: A chronological timeline of significant events in the case from start to finish
    
    Textual Entities:
    - Claimant: The party initiating the arbitration
    - Respondent: The party against whom the arbitration is initiated
    - Tribunal / Court: The arbitration tribunal, court, or authority handling the case
    - Governing Law: The applicable law governing the dispute
    - Industry Sector: Industry classification (Energy, Infrastructure, etc.)
    - Dispute Type: Type of dispute (breach of contract, pricing dispute, etc.)
    - Legal Claims: Key legal claims raised in the case
    - Legal Defenses: Key legal defenses raised in the case
    - Start Date: Date when lawsuit was filed
    - Date of Award: Final Date when the arbitral award was issued
    - Commodity: Commodities related to the dispute
    - Listed Stock Entity: Names of the listed stock entities
    - Currency: Currency in which the amounts are expressed

    Numerical Entities (provide only the number or null if not found):
    - Claim Amount: The amount claimed by the claimant
    - Contract Value: Value of the underlying contract in dispute
    - Legal Costs: The total cost incurred by both parties in arbitration
    - Interest Rate on Award: The interest rate imposed on the award
    - Regulatory Fines Imposed: Amount of fines or penalties levied
    - Award Amount: The final amount awarded by the tribunal

    For Case Description: Provide a comprehensive summary (250-500 words) covering the background, key issues, arguments, and outcome.
    For Case Timeline: Format as an array of objects with "date" and "event" fields, chronologically ordered.
    For dates, extract in YYYY-MM-DD format if possible.
    For numerical values, extract only the numbers without currency symbols or other text.
    If an entity is not found in the text, use null for its value.

    EXAMPLE JSON OUTPUT:
    {
        "Case Description": "The case involved a dispute between Modern Construction Company and the Boston Metropolitan Transit Authority over cost overruns and delays in the Big Dig infrastructure project. The contractor claimed that unforeseen site conditions and design changes led to significant additional costs and time delays. They sought compensation for these costs plus lost profits. The BMTA countered that the contractor failed to follow specifications and was negligent in project management. After a series of hearings spanning two years, the tribunal found partial merit in both parties' claims, awarding approximately 60% of the claimed amount to the contractor.",
        "Case Timeline": [
            {"date": "2003-11-10", "event": "Contract signed between Modern Construction and BMTA for tunnel construction"},
            {"date": "2004-03-15", "event": "Arbitration initiated by Modern Construction Company"},
            {"date": "2004-09-22", "event": "Initial hearing conducted"},
            {"date": "2005-07-22", "event": "Partial decision on liability issued"},
            {"date": "2005-07-22", "event": "Partial decision on liability issued"},
            {"date": "2006-02-14", "event": "Expert testimony on damages presented"},
            {"date": "2006-11-30", "event": "Final arbitral award issued"}
        ],
        "Claimant": "Modern Construction Company",
        "Respondent": "Boston Metropolitan Transit Authority",
        "Tribunal / Court": "American Arbitration Association",
        "Governing Law": "Massachusetts State Law",
        "Industry Sector": "Infrastructure",
        "Dispute Type": "Construction contract dispute",
        "Legal Claims": "Breach of contract, unforeseen site conditions, delay claims",
        "Legal Defenses": "Contractor negligence, failure to follow specifications",
        "Start Date": "2004-03-15",
        "Date of Award": "2006-11-30",
        "Interest Rate on Award": 5.75,
        "Commodity": [ {"ticker": "OIL"}],
        "Listed Stock Entity": [ {"ticker": "BMTA"},  {"ticker": "GOOG"}],
        "Legal Costs": 5200000,
        "Claim Amount": 32500000,
        "Contract Value": 145000000,
        "Regulatory Fines Imposed": 750000,
        "Award Amount": 18700000,
        "Currency": USD
    }
    """
    
    user_prompt = f"Extract named entities from the following text about '{search_phrase}':\n\n{content}"
    
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}]
    
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            response_format={
                'type': 'json_object'
            },
            temperature=0.1, 
        )
        
        entity_data = json.loads(response.choices[0].message.content)
   
        output_file = Path(output_directory) / "extracted_entities.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(entity_data, f, indent=2)
        
        print(f"Named entities extracted and saved to {output_file}")
        print("Extracted entities:")
        print(json.dumps(entity_data, indent=2))
        
    except Exception as e:
        print(f"Error processing content with DeepSeek API: {e}")

if __name__ == '__main__':
    asyncio.run(main())