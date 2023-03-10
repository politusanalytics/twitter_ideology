import pandas as pd
import sys
import os.path
from glob import glob
import sklearn.model_selection

# Inputs
input_folder_or_filename = sys.argv[1]
out_folder = sys.argv[2]

to_be_processed = []
if os.path.isdir(input_folder_or_filename):
    for filename in glob("{}/**/*.xlsx".format(input_folder_or_filename), recursive=True):
        to_be_processed.append(filename)
elif input_folder_or_filename.endswith(".xslx"):
    to_be_processed = [input_folder_or_filename]


all_df = pd.DataFrame()
for filename in to_be_processed:
    df = pd.read_excel(filename)
    annotators = df.Annotator.unique().tolist()
    annotators.pop(annotators.index("final"))
    final_annot = annotators[0]

    task_names = [col_name for col_name in df.columns if col_name.startswith("I") or col_name.startswith("B")]
    out_column_names = []
    for task_name in task_names:
        final_task_name = task_name.lower().replace("i\n", "").replace("b\n", "").replace("i \n", "").replace(" ", "_")
        out_column_names.append(final_task_name)
        # first set to one of the annotators
        df.loc[df.Annotator == "final", final_task_name] = df[df.Annotator == final_annot].reset_index(drop=True)[task_name].tolist()
        # then set to final annotator if available
        df.loc[(df.Annotator == "final") & (~df[task_name].isna()), final_task_name] = df[(df.Annotator == "final") & (~df[task_name].isna())].reset_index(drop=True)[task_name].tolist()

    df = df[df.Annotator == "final"]
    df = df[["#", "full_text"] + out_column_names]
    df = df.rename({"#": "id", "full_text": "text"}, axis=1)
    df.id = df.id.astype(int)
    all_df = all_df.append(df, ignore_index=True)


for col_name in out_column_names:
    all_df[col_name] = all_df[col_name].astype(int)

    if len(all_df[all_df[col_name] == 1]) < 2 or len(all_df[all_df[col_name] == 2]) < 2:
        print("train", col_name)
        train_df = all_df.sample(frac=1.0).reset_index(drop=True)
        split = int(len(train_df)*0.25)
        test_df = train_df.iloc[:split]
        train_df = train_df.iloc[split:]
    else:
        train_df, test_df = sklearn.model_selection.train_test_split(all_df, test_size=0.25, stratify=all_df[col_name])

    if len(test_df[test_df[col_name] == 1]) < 2 or len(test_df[test_df[col_name] == 2]) < 2:
        print("test", col_name)
        test_df = test_df.sample(frac=1.0).reset_index(drop=True)
        split = int(len(test_df)*0.4)
        dev_df = test_df.iloc[:split]
        test_df = test_df.iloc[split:]
    else:
        test_df, dev_df = sklearn.model_selection.train_test_split(test_df, test_size=0.40, stratify=test_df[col_name])

    train_df = train_df[["id", "text", col_name]].rename({col_name: "label"}, axis=1)
    test_df = test_df[["id", "text", col_name]].rename({col_name: "label"}, axis=1)
    dev_df = dev_df[["id", "text", col_name]].rename({col_name: "label"}, axis=1)

    train_df.to_json("{}/{}/train.json".format(out_folder, col_name), orient="records", lines=True, force_ascii=False)
    test_df.to_json("{}/{}/test.json".format(out_folder, col_name), orient="records", lines=True, force_ascii=False)
    dev_df.to_json("{}/{}/dev.json".format(out_folder, col_name), orient="records", lines=True, force_ascii=False)

all_df.to_json("{}/all.json".format(out_folder), orient="records", lines=True, force_ascii=False)
