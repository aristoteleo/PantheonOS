import requests
import json

def get_domain_for_single_gene(gene_name):
    url = "https://www.ncbi.nlm.nih.gov/research/pubtator-api/agentapi/cdd/?"
    params = {
        "name": gene_name,
        "retmode": "json",
        "limit": 10
        }
    response = requests.get(url, params=params)

    if response.status_code == 200:
        return json.dumps(response.json().get("results",{}))
    else:
        return f"Error: Unable to fetch data"


get_domain_for_single_gene_doc = {
	"name": "get_domain_for_single_gene",
	"description": "Given a gene name, return information on its related biological domains containing the domain id and the corresponding domain names.",
	"parameters": {
		"type": "object",
		"properties": {
			"gene_name": {
				"type": "string",
				"description": "A single gene name to search."
                }
            },
		"required": ["gene_name"],
	},
}


