from torch.utils.data import DataLoader
import torch
from tqdm import tqdm
import dgl
from graph_utils import bleuFormat, decode_output, get_node_features, tripl2graphw, fixed_decode_output
import json
from functools import partial
from dataset import collate_fn_captions, collate_fn_classifier, augmented_collate_fn, collate_fn_full, collate_fn_waterfall
from numpy import argmax
from torchmetrics.functional import f1_score
from pycocoevalcap.bleu.bleu import Bleu
from transformers import BertModel, BertTokenizer



def eval_captions(dataset, model, filename):
    '''
    Function that tests a model
    
    Args:
        dataset (torch.utils.data.Dataset): dataset to use for testing.
        model (torch.nn.Module): model to test on the dataset
        filename (str): name of the file in which the captions are saved
    
    Return:
        None
    '''
    testloader = DataLoader(dataset, batch_size=1, shuffle=False, collate_fn=partial(collate_fn_captions, word2idx=dataset.word2idx, training=True))
    # Set the correct device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    # Create the conversion id -> token
    idx2word = {v: k for k, v in dataset.word2idx.items()}
    with torch.no_grad():
        model.eval()
        result = {}
        for _, data in enumerate(tqdm(testloader)):
            ids, _, encoded_captions, src_ids, dst_ids, node_feats, num_nodes = data
            graphs = dgl.batch([dgl.graph((src_id, dst_id)) for src_id, dst_id in zip(src_ids, dst_ids)]).to(device)
            feats = get_node_features(node_feats, sum(num_nodes)).to(device)
            outputs = model(graphs, feats, encoded_captions)
            decoded_outputs = decode_output(outputs, idx2word)
            for i, id in enumerate(ids):
                result[id] = {"caption length": len(decoded_outputs[i]),"caption ": decoded_outputs[i]}
            
    with open(filename, "w") as outfile:
        json.dump(result, outfile)
        

def augmented_eval_captions(dataset, model, filename):
    '''
    Function that tests a model
    
    Args:
        dataset (torch.utils.data.Dataset): dataset to use for testing.
        model (torch.nn.Module): model to test on the dataset
        filename (str): name of the file in which the captions are saved
    
    Return:
        None
    '''
    testloader = DataLoader(dataset, batch_size=1, shuffle=False, collate_fn=partial(augmented_collate_fn, word2idx=dataset.word2idx, training=True))
    # Set the correct device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    # Create the conversion id -> token
    idx2word = {v: k for k, v in dataset.word2idx.items()}
    with torch.no_grad():
        model.eval()
        result = {}
        for _, data in enumerate(tqdm(testloader)):
            ids, images, _, encoded_captions, src_ids, dst_ids, node_feats, num_nodes = data
            graphs = dgl.batch([dgl.graph((src_id, dst_id)) for src_id, dst_id in zip(src_ids, dst_ids)]).to(device)
            feats = get_node_features(node_feats, sum(num_nodes)).to(device)
            img = images.to(device)
            outputs = model(graphs, feats, img, encoded_captions)
            decoded_outputs = decode_output(outputs, idx2word)
            for i, id in enumerate(ids):
                result[id] = {"caption length": len(decoded_outputs[i]),"caption ": decoded_outputs[i]}
            
    with open(filename, "w") as outfile:
        json.dump(result, outfile)
    
    bleuFormat(filename)


def eval_classification(dataset, model, filename, verbose=False):
    '''
    Function that tests a model
    
    Args:
        dataset (torch.utils.data.Dataset): dataset to use for testing.
        model (torch.nn.Module): model to test on the dataset
        filename (str): name of the file in which the captions are saved
    
    Return:
        None
    '''
    # Create the dataloader
    testloader = DataLoader(dataset, batch_size=1, shuffle=False, collate_fn=partial(collate_fn_classifier, triplet_to_idx=dataset.triplet_to_idx))
    # Set the correct device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    # Create the conversion id -> triplet
    idx2triplet = {v: k for k, v in dataset.triplet_to_idx.items()}
    with torch.no_grad():
        model.eval()
        accuracy_test = 0 
        for i, data in enumerate(tqdm(testloader)):
            images, triplets = data
            images = images.to(device)
            triplets = triplets.to(device)
            outputs = model(images)
            # Reshape with the right size
            outputs = outputs.reshape((outputs.shape[0], int(outputs.shape[1]/2), 2))
            # Calculate accuracy on training
            outputs = torch.sigmoid(outputs)
            outputs = torch.tensor([[torch.argmax(task).item() for task in sample ] for sample in outputs]).to(outputs.device)
            accuracy = f1_score(outputs, triplets.long(), num_classes=2, mdmc_average='global')
            
            
            outputs = outputs.nonzero()
            triplets = triplets.nonzero()
            
            if(True):
                print('True triplets')
                for i in range(triplets.shape[0]):
                    print(idx2triplet[triplets[i][1].item()])
                print('Predicted triplets')
                for i in range(outputs.shape[0]):
                    print(idx2triplet[outputs[i][1].item()])
                    
            accuracy_test += accuracy
    
    print('Test accuracy: {:.3f}'.format(accuracy_test/i))
        
        
        
def eval_pipeline(dataset, model, filename, pil):
    '''
    Function that tests a model
    
    Args:
        dataset (torch.utils.data.Dataset): dataset to use for testing.
        model (torch.nn.Module): model to test on the dataset
        filename (str): name of the file in which the captions are saved
    
    Return:
        None
    '''
    testloader = DataLoader(dataset, batch_size=1, shuffle=False, collate_fn=partial(collate_fn_full, triplet_to_idx=dataset.triplet_to_idx, word2idx=dataset.word2idx, training=True, pil=pil))
    # Set the correct device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    # Create the conversion id -> token
    idx2word = {v: k for k, v in dataset.word2idx.items()}
    with torch.no_grad():
        model.eval()
        result = {}
        for _, data in enumerate(tqdm(testloader)):
            ids, images, _, captions, encoded_captions, lengths, _, _, _, _ = data
            images = images.to(device)
            cap_output = model.sample(images)
            # decoded_outputs = decode_output(cap_outputs, idx2word)
            #decoded_outputs = fixed_decode_output(cap_output, idx2word)
            decode_output = [idx2word[idx] for idx in cap_output]
            for _, id in enumerate(ids):
                result[id] = {"caption length": len(decode_output),"caption ": decode_output}
            
    with open(filename, "w") as outfile:
        json.dump(result, outfile) 
    # Transform the output in bleu Format for the evaluation
    bleuFormat(filename)
    
def eval_waterfall(dataset, model, filename, pil):
    '''
    Function that tests a model
    
    Args:
        dataset (torch.utils.data.Dataset): dataset to use for testing.
        model (torch.nn.Module): model to test on the dataset
        filename (str): name of the file in which the captions are saved
    
    Return:
        None
    '''
    testloader = DataLoader(dataset, batch_size=1, shuffle=False, collate_fn=partial(collate_fn_waterfall, word2idx=dataset.word2idx, training=True, pil=pil))
    # Set the correct device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    feature_encoder = BertModel.from_pretrained("bert-base-uncased")
    tokenizer = BertTokenizer.from_pretrained("bert-base-uncased")
    # Create the conversion id -> token
    idx2word = {v: k for k, v in dataset.word2idx.items()}
    with torch.no_grad():
        model.eval()
        result = {}
        for _, data in enumerate(tqdm(testloader)):
            ids, _, triplets, _, _, _ = data
            graphs, graph_feats = tripl2graphw(triplets, feature_encoder, tokenizer)
            graphs, graph_feats = graphs.to(device), graph_feats.to(device)
            # img = img.to(device)
            outputs = model.sample(graphs, graph_feats)
            # decoded_outputs = fixed_decode_output(outputs, idx2word)
            decoded_output = [idx2word[idx] for idx in outputs]
            for _, id in enumerate(ids):
                result[id] = {"caption length": len(decoded_output),"caption ": decoded_output}
            
    with open(filename, "w") as outfile:
        json.dump(result, outfile)
    # Transform the output in bleu Format for the evaluation
    bleuFormat(filename)
    

def eval_predictions(predictions, ground_truth):
    '''
    Function that tests a model
    Args:
        list_of_predictions (list): list of predictions
        list_true (list): list of lists where each element contains the reference ground truth(s)
    
    Return:
        print the bleu scores
    '''
    scorers = [
            (Bleu(4), ["Bleu_1", "Bleu_2", "Bleu_3", "Bleu_4"]),
        ]
    for scorer, _ in scorers:
        score, _ = scorer.compute_score(ground_truth, predictions)
        score = [str(round(sc*100,2)) for sc in score]
        for i in range(len(score)):
            print('BLEU '+str(i))
            print(score[i])
    
    return


if __name__ == "__main__":
    import json
    
    # Load the predictions
    with open('w_mlap_rnn_captions.json','r') as file:
        predictions = json.load(file)
    
    for key, value in predictions.items():
        predictions[key] = [' '.join(value)]
        
    # Parse the UCM captions
    ground_truth_captions = dict()
    with open('dataset/UCM_dataset/filenames/descriptions_UCM.txt', 'r') as file:
        for line in file.readlines():
            pieces = line.split(' ')
            if(pieces[0] in predictions.keys()):
                try:
                    ground_truth_captions[pieces[0]].append(' '.join(pieces[1:]).strip())
                except:
                    ground_truth_captions[pieces[0]] = [' '.join(pieces[1:]).strip()]
        
    eval_predictions(predictions, ground_truth_captions)