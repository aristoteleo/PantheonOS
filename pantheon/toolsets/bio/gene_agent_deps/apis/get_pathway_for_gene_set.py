import json
import requests

def get_pathway_for_gene_set(gene_set):
    """
    The returned values are Rank, Term name, P-value, Odds ratio, Combined score, Overlapping genes, Adjusted p-value, Old p-value, Old adjusted p-value
    """
    gene_set = gene_set.replace(" ","")
    gene_list = gene_set.split(",")

    ENRICHR_URL_ADD = 'http://maayanlab.cloud/Enrichr/addList'
    payload = {
        'list': (None, '\n'.join(gene_list)),
        'description': (None, 'My gene set')
    }
    response_add = requests.post(ENRICHR_URL_ADD, files=payload)

    if not response_add.ok:
        raise Exception('Error adding list to Enrichr:', response_add.text)

    data = json.loads(response_add.text)
    list_id = data['userListId']
    dic = {}
    for backgroundType in ["KEGG_2021_Human", "Reactome_2022", "BioPlanet_2019", "MSigDB_Hallmark_2020"]:
        try:
            ENRICHR_URL_RESULTS = f'http://maayanlab.cloud/Enrichr/enrich?userListId={list_id}&backgroundType={backgroundType}'
            response_results = requests.get(ENRICHR_URL_RESULTS)
            
            if not response_results.ok:
                raise Exception('Error fetching pathway results:', response_results.text)
            
            pathway_data = response_results.json()[backgroundType]
            for value in pathway_data[:3]:
                dic[value[1]] = [value[2],",".join(value[5]), backgroundType]
        except TypeError:
            continue
    pathway_analysis = []    
    dic_sorted = dict(sorted(dic.items(), key=lambda item: item[1][0]))
    for key, value in dic_sorted.items():
        pathway_analysis.append({"term": key, "overlapping genes": value[1], "database": value[2]})
        # print(pathway_analysis)
    return json.dumps(pathway_analysis[:5])

get_pathway_for_gene_set_doc = {
    "name": "get_pathway_for_gene_set",
    "description": "Given a gene set, return its top-5 biological pathway names.",
    "parameters": {
        "type": "object",
        "properties": {
            "gene_set": {
                "type": "string",
                "description": "A gene set splittd only by comma \",\" (must no whitespace) to search. For example, \"x,y,z\".",
            }
        },
        "required": ["gene_set"],
    },
}


