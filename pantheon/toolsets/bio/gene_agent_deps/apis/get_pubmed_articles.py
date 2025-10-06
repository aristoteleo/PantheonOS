import json
import requests
from xml.etree import ElementTree

def get_pubmed_articles(term):
    base_url_pubmed = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    search_url = f"{base_url_pubmed}/esearch.fcgi"
    fetch_url = f"{base_url_pubmed}/efetch.fcgi"
    search_params = {
        "db": "pubmed",
        "term": term,
        "retmode": "xml",
        "retmax": "5",
        "sort": "relevance"
    }
    search_response = requests.get(search_url, params=search_params)
    try:
        search_results = ElementTree.fromstring(search_response.content)
        id_list = [id_tag.text for id_tag in search_results.findall('.//Id')]
    except ElementTree.ParseError as e:
        return f"Error parsing search results: {e}"
    
    if not id_list:
        return "No articles found for the query."
    
    fetch_params = {
        "db": "pubmed",
        "id": ",".join(id_list),
        "retmode": "xml"
    }
    fetch_response = requests.get(fetch_url, params=fetch_params)
    
    try:
        articles = ElementTree.fromstring(fetch_response.content)
    except ElementTree.ParseError as e:
        return f"Error parsing fetch results: {e}"

    results = [] 
    for article in articles.findall('.//PubmedArticle'):
        pmid_elem = article.find('.//PMID')
        pmid = pmid_elem.text if pmid_elem is not None else "No PMID available"
        title_elem = article.find('.//ArticleTitle')
        title = title_elem.text if title_elem is not None else "No title available"
        abstract_elem = article.find('.//Abstract/AbstractText')
        abstract_text = abstract_elem.text if abstract_elem is not None else "No abstract available"
        results.append(f"PMID: {pmid}\nTitle: {title}\nAbstract: {abstract_text}\n")
    return "".join(results)


get_pubmed_articles_doc = {
    "name": "get_pubmed_articles",
    "description": "Given a PubMed ID, return related PubMed articles containing titles and abstractions.",
    "parameters": {
        "type": "object",
        "properties": {
            "term": {
                "type": "string",
                "description": "a pubmed ID to search.",
            },
        },
        "required": ["term"],
    },
}  
