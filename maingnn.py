from dataset import UCMTriplets, collate_fn_captions
from transformers import BertTokenizer, BertModel
from models import CaptionGenerator
from train import caption_trainer

train_filenames = 'D:/Alessio/Provone/dataset/UCM_dataset/filenames/filenames_train.txt'
val_filenames = 'D:/Alessio/Provone/dataset/UCM_dataset/filenames/filenames_val.txt'
img_path = 'D:/Alessio/Provone/dataset/UCM_dataset/images/'
tripl_path = 'triplets.json'
anno_path = 'D:/Alessio/Provone/dataset/UCM_dataset/filenames/descriptions_UCM.txt'
tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')
model = BertModel.from_pretrained("bert-base-uncased")
return_k = ['src_ids', 'dst_ids', 'node_feats', 'captions', 'num_nodes']

train_dataset = UCMTriplets(img_path, train_filenames, tripl_path, anno_path, model, tokenizer, return_keys=return_k, split='train')
val_dataset = UCMTriplets(img_path, val_filenames, tripl_path, anno_path, model, tokenizer, return_keys=return_k, split='val')
feats_n = train_dataset.node_feats['1'][0].size(0)
max = train_dataset.max_capt_length
if val_dataset.max_capt_length>max:
    max = val_dataset.max_capt_length

word2idx = train_dataset.word2idx

model = CaptionGenerator(feats_n, max, word2idx)
trainer = caption_trainer(model,train_dataset,val_dataset,collate_fn_captions, word2idx, max, 'GNN.pth')
trainer.fit(20, 0.0001, 8, model._loss)