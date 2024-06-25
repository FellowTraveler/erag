import spacy
import networkx as nx
import json
from sentence_transformers import SentenceTransformer, util
import torch
import logging
from typing import List, Tuple, Dict
from nltk.tokenize import sent_tokenize
import nltk
from embeddings_utils import load_embeddings_and_data

nltk.download('punkt', quiet=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

nlp = spacy.load("en_core_web_sm")
model = SentenceTransformer("all-MiniLM-L6-v2")

FAMILY_RELATIONS = [
    "sister", "sisters", "brother", "brothers", "father", "mother", "parent", "parents",
    "son", "sons", "daughter", "daughters", "husband", "wife", "spouse",
    "grandfather", "grandmother", "grandparent", "grandparents",
    "grandson", "granddaughter", "grandchild", "grandchildren",
    "uncle", "aunt", "cousin", "cousins", "niece", "nephew"
]

def preprocess_text(text: str) -> str:
    return ' '.join(text.split())

def extract_key_entities(text: str) -> List[Tuple[str, str]]:
    doc = nlp(text)
    key_entities = []
    for ent in doc.ents:
        if ent.label_ in ['PERSON', 'ORG', 'GPE', 'LOC', 'PRODUCT', 'EVENT', 'WORK_OF_ART']:
            key_entities.append((ent.text, ent.label_))
    return key_entities

def extract_relations(text: str, key_entities: List[Tuple[str, str]]) -> List[Tuple[str, str, str]]:
    doc = nlp(text)
    triples = []
    entity_spans = []
    
    for ent, _ in key_entities:
        start = text.find(ent)
        if start != -1:
            end = start + len(ent)
            span = doc.char_span(start, end)
            if span:
                entity_spans.append(span)
    
    for sent in doc.sents:
        sent_entities = [span for span in entity_spans if span.start >= sent.start and span.end <= sent.end]
        for i, entity1 in enumerate(sent_entities):
            for j in range(i+1, len(sent_entities)):
                entity2 = sent_entities[j]
                path = list(entity1.root.subtree) + list(entity2.root.subtree)
                path = sorted(set(path), key=lambda x: x.i)
                relation = ' '.join([token.lemma_ for token in path if token.pos_ in ['VERB', 'ADP']])
                
                # Check for family relations
                family_relation = next((rel for rel in FAMILY_RELATIONS if rel in sent.text.lower()), None)
                if family_relation:
                    relation = family_relation
                
                if relation:
                    triples.append((entity1.text, relation, entity2.text))
    
    return triples

def extract_family_relations(text: str) -> List[Tuple[str, str, str]]:
    doc = nlp(text)
    family_triples = []
    
    for sent in doc.sents:
        entities = [ent for ent in sent.ents if ent.label_ == 'PERSON']
        for i, entity1 in enumerate(entities):
            for j in range(i+1, len(entities)):
                entity2 = entities[j]
                for relation in FAMILY_RELATIONS:
                    if relation in sent.text.lower():
                        family_triples.append((entity1.text, relation, entity2.text))
                        # Add reverse relation
                        if relation in ["sister", "brother"]:
                            family_triples.append((entity2.text, relation, entity1.text))
                        elif relation == "parent":
                            family_triples.append((entity2.text, "child", entity1.text))
                        elif relation == "child":
                            family_triples.append((entity2.text, "parent", entity1.text))
    
    return family_triples

def create_networkx_graph(data: List[str], embeddings: torch.Tensor) -> nx.Graph:
    G = nx.Graph()
    
    for i, chunk in enumerate(data):
        chunk = preprocess_text(chunk)
        key_entities = extract_key_entities(chunk)
        triples = extract_relations(chunk, key_entities)
        family_triples = extract_family_relations(chunk)
        
        doc_node = f"doc_{i}"
        G.add_node(doc_node, type='document', text=chunk)
        
        for entity, entity_type in key_entities:
            if not G.has_node(entity):
                G.add_node(entity, type='entity', entity_type=entity_type)
            G.add_edge(doc_node, entity, relation='contains')
        
        for subj, rel, obj in triples + family_triples:
            G.add_edge(subj, obj, relation=rel)
    
    # Add semantic similarity edges between document nodes
    doc_nodes = [n for n, d in G.nodes(data=True) if d['type'] == 'document']
    for i, node1 in enumerate(doc_nodes):
        for j in range(i+1, len(doc_nodes)):
            node2 = doc_nodes[j]
            similarity = util.pytorch_cos_sim(embeddings[i], embeddings[j]).item()
            if similarity > 0.7:  # Increased threshold for stronger connections
                G.add_edge(node1, node2, relation='similar', weight=similarity)
    
    return G

def prune_graph(G: nx.Graph, min_edge_weight: float = 0.7) -> nx.Graph:
    edges_to_remove = [(u, v) for (u, v, d) in G.edges(data=True) 
                       if 'weight' in d and d['weight'] < min_edge_weight]
    G.remove_edges_from(edges_to_remove)
    return G

def save_graph_json(G: nx.Graph, file_path: str):
    graph_data = nx.node_link_data(G)
    with open(file_path, 'w', encoding='utf-8') as file:
        json.dump(graph_data, file, indent=2)

def create_knowledge_graph():
    embeddings, _, content = load_embeddings_and_data("db_embeddings.pt")
    
    if embeddings is None or content is None:
        logging.error("Failed to load data from db_embeddings.pt")
        return None

    G = create_networkx_graph(content, embeddings)
    G = prune_graph(G)
    save_graph_json(G, "knowledge_graph.json")
    logging.info(f"NetworkX graph created with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")
    logging.info("Graph saved as knowledge_graph.json")
    return G

if __name__ == "__main__":
    create_knowledge_graph()
