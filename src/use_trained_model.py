"""
This script is used to predict big files with a trained model from train.py.
You can already do prediction in train.py, but this script lazy loads a file
so that we can process bigger files.
"""

from transformers import AutoModel, AutoTokenizer, AutoConfig
from commons import MLP
import sys
import numpy as np
import json
import torch
import gzip

# INPUTS
database_or_input_filename = sys.argv[1]

# MUST SET THESE VALUES
output_filename = "out.json"
pretrained_transformers_model = "dbmdz/bert-base-turkish-128k-cased"
max_seq_length = 64
batch_size = 1536
repo_path = "/home/username/twitter_ideology"

module_name = "ideology_2"

if module_name == "ideology_1":
    idx_to_label = ["turkish_nationalism", "conservatism", "islamism", "liberalism", "kemalism"]
    encoder_path = "{}/models/best_models/multi_label/encoder_dbmdz_bert-base-turkish-128k-cased_ideology_1_43.pt".format(repo_path)
    classifier_path = "{}/models/best_models/multi_label/classifier_dbmdz_bert-base-turkish-128k-cased_ideology_1_43.pt".format(repo_path)
elif module_name == "ideology_2":
    idx_to_label = ["social_democracy", "socialism", "feminism", "environmentalism",
                    "kurdish_national_movement", "secularism"]
    encoder_path = "{}/models/best_models/multi_label/encoder_dbmdz_bert-base-turkish-128k-cased_ideology_2_62.pt".format(repo_path)
    classifier_path = "{}/models/best_models/multi_label/classifier_dbmdz_bert-base-turkish-128k-cased_ideology_2_62.pt".format(repo_path)

device = torch.device("cuda")

# OPTIONS
return_probabilities = False
positive_threshold = 0.5

# LOAD MODEL
tokenizer = AutoTokenizer.from_pretrained(pretrained_transformers_model)
config = AutoConfig.from_pretrained(pretrained_transformers_model)
has_token_type_ids = config.type_vocab_size > 1

encoder = AutoModel.from_pretrained(pretrained_transformers_model)
encoder.to(device)
encoder.load_state_dict(torch.load(encoder_path, map_location=device))
classifier = MLP(encoder.config.hidden_size, encoder.config.hidden_size*4, len(idx_to_label))
classifier.to(device)
classifier.load_state_dict(torch.load(classifier_path, map_location=device))

encoder = torch.nn.DataParallel(encoder)
encoder.eval()
classifier.eval()

def model_predict(batch):
    if has_token_type_ids:
        input_ids, input_mask, token_type_ids = tuple(t.to(device) for t in batch.values())
        embeddings = encoder(input_ids, attention_mask=input_mask, token_type_ids=token_type_ids)[1]
    else:
        input_ids, input_mask = tuple(t.to(device) for t in batch.values())
        embeddings = encoder(input_ids, attention_mask=input_mask)[1]

    out = classifier(embeddings)

    all_preds = torch.sigmoid(out).detach().cpu().numpy().tolist()
    if return_probabilities:
        all_preds = [[round(float(x), 4) for x in preds] for preds in all_preds]
    else:
        all_preds = [[idx_to_label[i] for i, x in enumerate(preds) if x >= positive_threshold] for preds in all_preds]

    return all_preds

def preprocess(text): # Preprocess text (username and link placeholders)
    new_text = []
    for t in text.split(" "):
        t = '@user' if t.startswith('@') and len(t) > 1 else t
        t = 'http' if t.startswith('http') else t
        new_text.append(t)
    return " ".join(new_text)

# !!!IMPORTANT!!!
# Change this according to the json line format.
# Here the format for every line is like:
# {id_str: text}
def read_json_line(data):
    id_str = list(data.keys())[0]
    text = preprocess(data[id_str])

    return id_str, text

if __name__ == "__main__":
    if database_or_input_filename == "database": # if database
        import pymongo

        # Connect to mongodb
        mongo_client = pymongo.MongoClient("mongodb://localhost:27017/")
        db = mongo_client["politus_twitter"]
        tweet_col = db["tweets"]

        # NOTE: This find can be changed according to the task.
        tweets_to_predict = tweet_col.find({module_name: None}, ["_id", "text"])

        curr_batch = []
        for i, tweet in enumerate(tweets_to_predict):
            id_str = tweet["_id"]
            text = preprocess(tweet["text"])

            if len(text) > 0:
                curr_batch.append({"_id": id_str, "text": text})

            if len(curr_batch) == batch_size:
                texts = [d["text"] for d in curr_batch]
                inputs = tokenizer(texts, return_tensors="pt", padding="max_length", truncation=True,
                                   max_length=max_seq_length)
                preds = model_predict(inputs)
                # TODO: Think about multiple updates at the same time
                for pred_idx, pred in enumerate(preds):
                    curr_d = curr_batch[pred_idx]
                    tweet_col.update_one({"_id": curr_d["_id"]}, {"$set": {module_name: pred}})

                curr_batch = []

        # Last incomplete batch, if any
        if len(curr_batch) != 0:
            texts = [d["text"] for d in curr_batch]
            inputs = tokenizer(texts, return_tensors="pt", padding="max_length", truncation=True,
                               max_length=max_seq_length)
            preds = model_predict(inputs)
            for pred_idx, pred in enumerate(preds):
                curr_d = curr_batch[pred_idx]
                tweet_col.update_one({"_id": curr_d["_id"]}, {"$set": {module_name: pred}})

    else: # if filename

        output_file = open(output_filename, "w", encoding="utf-8")
        if database_or_input_filename.endswith(".json.gz"):
            input_file = gzip.open(database_or_input_filename, "rt", encoding="utf-8")
        elif database_or_input_filename.endswith(".json"):
            input_file = open(database_or_input_filename, "r", encoding="utf-8")
        else:
            raise("File extension should be 'json' or 'json.gz'!")

        curr_batch = []
        for i, line in enumerate(input_file):
            data = json.loads(line)
            id_str, text = read_json_line(data)

            if len(text) > 0:
                curr_batch.append({"id_str": id_str, "text": text})

            if len(curr_batch) == batch_size:
                texts = [d["text"] for d in curr_batch]
                inputs = tokenizer(texts, return_tensors="pt", padding="max_length", truncation=True,
                                   max_length=max_seq_length)
                preds = model_predict(inputs)
                for pred_idx, pred in enumerate(preds):
                    curr_d = curr_batch[pred_idx]
                    curr_d.pop("text") # No need for text in the output.
                    curr_d["prediction"] = pred
                    output_file.write(json.dumps(curr_d, ensure_ascii=False) + "\n")

                curr_batch = []

        input_file.close()

        # Last incomplete batch, if any
        if len(curr_batch) != 0:
            texts = [d["text"] for d in curr_batch]
            inputs = tokenizer(texts, return_tensors="pt", padding="max_length", truncation=True,
                               max_length=max_seq_length)
            preds = model_predict(inputs)
            for pred_idx, pred in enumerate(preds):
                curr_d = curr_batch[pred_idx]
                curr_d.pop("text") # No need for text in the output.
                curr_d["prediction"] = pred
                output_file.write(json.dumps(curr_d, ensure_ascii=False) + "\n")

        output_file.close()
