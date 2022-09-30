from turtle import forward
import torch
import torch.nn as nn
from torchvision.models import resnet152, ResNet152_Weights
from gnn import GNN, LSTMDecoder, _encode_seq_to_arr, decoderRNN, MLAPModel
from transformers import BertModel, BertTokenizer
from graph_utils import tripl2graph

class TripletClassifier(nn.Module):
    '''
    Model which takes as input an image and predict the corresponding tripts that are in that image. 
    It will be based on resnet-152 for the extraction of the features, so it will be a finetuning on the target dataset.
    '''
    def __init__(self, input_size, num_classes):
        super(TripletClassifier, self).__init__()
        self.input_size = input_size
        self.num_classes = num_classes
        weights = ResNet152_Weights.DEFAULT
        self.model = resnet152(weights=weights)
        self.preprocess = weights.transforms()
        # Replace the last layer with a new layer for classification
        self.model.fc = nn.Sequential(
            nn.Linear(in_features=2048,out_features=1024),
            nn.Dropout(0.3),
            nn.Linear(in_features=1024,out_features=num_classes)
            )
        
        # Freeze all the layers except the fully connected
        for name, parameter in self.model.named_parameters():
            if(not 'fc' in name):
                parameter.requires_grad = False
    
    def forward(self, x):
        '''
        x -> shape (batch_size, channels, width, height)
        '''
        assert x.shape[2]==self.input_size
        assert x.shape[3]==self.input_size
        
        return self.model(self.preprocess(x))

def load_model(path):
    return torch.load(path)
    
class CaptionGenerator(nn.Module):
    '''
    Caption generation network (encoder-decoder)

    Args:
        feats_dim: dimension of the features
        max_seq_len: maximum tokens in a caption
        vocab2idx: dictionary for one hot encoding of tokens
        decoder: type of decoder (linear, lstm or rnn)
    '''
    def __init__(self, feats_dim, max_seq_len, vocab2idx, decoder='lstm') -> None:
        super(CaptionGenerator, self).__init__()
        self.encoder = GNN(feats_dim)
        self.decoder_type = decoder
        if self.decoder_type == 'linear':
            self.decoder = nn.ModuleList([nn.Linear(feats_dim, len(vocab2idx)) for _ in range(max_seq_len)])
        if self.decoder_type == 'lstm':
            self.decoder = LSTMDecoder(feats_dim, max_seq_len, vocab2idx)
        if self.decoder_type == 'rnn':
            self.decoder = decoderRNN(feats_dim, len(vocab2idx), feats_dim, 3)
        self.dropout = nn.Dropout(p=0.3)
        self.vocab2idx = vocab2idx
        self.idx2vocab = {v: k for k, v in vocab2idx.items()}

    def forward(self, g, feats, labels):
        graph_feats = self.dropout(self.encoder(g, feats))
        
        if self.decoder_type == 'linear':
            decoded_out = [d(graph_feats) for d in self.decoder]
        if self.decoder_type == 'lstm':
            decoded_out = self.decoder(g, graph_feats, labels)
        if self.decoder_type == 'rnn':
            decoded_out = self.decoder(graph_feats, labels)
        return decoded_out

    def _loss(self, out, labels, vocab2idx, max_seq_len, device) -> torch.Tensor:
        batched_label = torch.vstack([_encode_seq_to_arr(label, vocab2idx, max_seq_len) for label in labels])
        return sum([nn.CrossEntropyLoss()(out[i], batched_label[:, i].to(device=device)) for i in range(max_seq_len)])/max_seq_len



class AugmentedCaptionGenerator(nn.Module):
    '''
    Caption generation network (encoder-decoder)

    Args:
        feats_dim: dimension of the features
        max_seq_len: maximum tokens in a caption
        vocab2idx: dictionary for one hot encoding of tokens
        decoder: type of decoder (linear, lstm or rnn)
    '''
    def __init__(self, img_encoder, feats_dim, max_seq_len, vocab2idx, gnn='gat', vir=True, depth=1, decoder='lstm') -> None:
        super(AugmentedCaptionGenerator, self).__init__()
        self.encoder = GNN(feats_dim)
        
        # Incorporate image in the pipeline
        self.img_encoder = img_encoder
        self.img_encoder.model.fc = nn.Linear(2048, feats_dim)
        # Freeze all the layers except the fully connected
        for name, parameter in self.img_encoder.named_parameters():
            if(not 'fc' in name):
                parameter.requires_grad = False

        # Initialize the weight at a random value
        self.img_weight = torch.nn.parameter.Parameter(torch.randn(1, requires_grad=True))
        
        self.decoder_type = decoder
        if self.decoder_type == 'linear':
            self.decoder = nn.ModuleList([nn.Linear(feats_dim, len(vocab2idx)) for _ in range(max_seq_len)])
        if self.decoder_type == 'lstm':
            self.decoder = LSTMDecoder(feats_dim, max_seq_len, vocab2idx)
        if self.decoder_type == 'rnn':
            self.decoder = decoderRNN(feats_dim, len(vocab2idx), feats_dim, 3)
        self.dropout = nn.Dropout(p=0.3)
        self.vocab2idx = vocab2idx
        self.idx2vocab = {v: k for k, v in vocab2idx.items()}

    def forward(self, g, g_feats, img, labels=None):
        i_feats = self.img_encoder(img)
        
        graph_feats = self.dropout(self.encoder(g, g_feats))
        mod_feats = graph_feats + (i_feats * self.img_weight)
        
        if self.decoder_type == 'linear':
            decoded_out = [d(mod_feats) for d in self.decoder]
        if self.decoder_type == 'lstm':
            decoded_out = self.decoder(g, mod_feats, labels)
        if self.decoder_type == 'rnn':
            decoded_out = self.decoder(mod_feats, labels)
        return decoded_out

    def _loss(self, out, labels, vocab2idx, max_seq_len, device) -> torch.Tensor:
        batched_label = torch.vstack([_encode_seq_to_arr(label, vocab2idx, max_seq_len) for label in labels])
        return sum([nn.CrossEntropyLoss()(out[i], batched_label[:, i].to(device=device)) for i in range(max_seq_len)])/max_seq_len
    


class MultiHead(torch.nn.Module):
    '''
    Class for the multihead classifier for the triplet prediction
    
    Args:
        backbone (torch.nn.Module): backbone for the images
        heads List[torch.nn.Module]: list of heads for the tasks
    '''
    def __init__(self, backbone, heads):
        super().__init__()
        self.backbone = backbone
        # Initializing all the heads as part of a ModuleList
        self.heads = torch.nn.ModuleList(heads)

    def forward(self, x):
        common_features = self.backbone(x)  # compute the shared features
        outputs = [head(common_features) for head in self.heads]
        outputs = torch.cat(outputs, dim=1)
        return outputs


class MultiHeadClassifier(nn.Module):
    
    def __init__(self, input_size, dict_size):
        super(MultiHeadClassifier, self).__init__()
        self.input_size = input_size
        weights = ResNet152_Weights.DEFAULT
        self.backbone = resnet152(weights=weights)
        self.preprocess = weights.transforms()
        classifiers = [torch.nn.Linear(2048, 2) for _ in range(dict_size)]
        self.backbone.fc = MultiHead(torch.nn.Identity(), classifiers)
        # Freeze all the layers except the fully connected
        for name, parameter in self.backbone.named_parameters():
            if(not 'fc' in name):
                parameter.requires_grad = False
        
    def forward(self, img):
        
        assert img.shape[2]==self.input_size
        assert img.shape[3]==self.input_size
        
        features = self.backbone(self.preprocess(img))
        
        return features
    
class FinalModel(nn.Module):
    '''
    Caption generation network (encoder-decoder)

    Args:
        feats_dim: dimension of the features
        max_seq_len: maximum tokens in a caption
        vocab2idx: dictionary for one hot encoding of tokens
        decoder: type of decoder (linear, lstm or rnn)
    '''
    def __init__(self, img_encoder, feats_dim, max_seq_len, vocab2idx, img_dim, tripl2idx, gnn='gat', res=False, vir=True, depth=1, decoder='lstm') -> None:
        super(FinalModel, self).__init__()
        if gnn == 'gat' or gnn == 'gcn':
            self.graph_encoder = GNN(feats_dim, gnn)
        elif gnn == 'mlap':
            self.graph_encoder = MLAPModel(res, vir, feats_dim, depth)
        self.tripl_classifier = MultiHeadClassifier(img_dim, len(tripl2idx))
        # self.tripl_classifier = TripletClassifier(img_dim, len(tripl2idx))
        self.sigmoid = nn.Sigmoid()
        self.idx2tripl = {v: k for k, v in tripl2idx.items()}
        self.feature_encoder = BertModel.from_pretrained("bert-base-uncased")
        self.tokenizer = BertTokenizer.from_pretrained("bert-base-uncased")
        # Incorporate image in the pipeline
        self.img_encoder = img_encoder
        self.img_encoder.model.fc = nn.Linear(2048, feats_dim)
        # Freeze all the layers except the fully connected
        for name, parameter in self.img_encoder.named_parameters():
            if(not 'fc' in name):
                parameter.requires_grad = False

        # Initialize the weight at a random value
        self.img_weight = torch.nn.parameter.Parameter(torch.randn(1, requires_grad=True))
        
        self.decoder_type = decoder
        if self.decoder_type == 'linear':
            # self.decoder = nn.ModuleList([nn.Linear(feats_dim, len(vocab2idx)) for _ in range(max_seq_len)])
            # For modified concatenation
            self.decoder = nn.ModuleList([nn.Linear(feats_dim*2, len(vocab2idx)) for _ in range(max_seq_len)])
        if self.decoder_type == 'lstm':
            self.decoder = LSTMDecoder(feats_dim, max_seq_len, vocab2idx)
        if self.decoder_type == 'rnn':
            self.decoder = decoderRNN(feats_dim, len(vocab2idx), feats_dim, 3)
        self.dropout = nn.Dropout(p=0.3)
        self.vocab2idx = vocab2idx
        self.idx2vocab = {v: k for k, v in vocab2idx.items()}

    def forward(self, img, labels=None, training=False):
        # Triplet classification
        triplets = self.sigmoid(self.tripl_classifier(img))
        # For normal classifier
        # class_out = triplets
        # # For multihead classifier
        triplets = triplets.reshape((triplets.shape[0], int(triplets.shape[1]/2), 2))
        class_out = triplets
        triplets = [[torch.argmax(logits).item() for logits in img] for img in triplets]
        # Changed for BCE loss
        # Extract indeces greater or equal than the threshold
        threshold = 0.5
        indeces = [[ i for i, d in enumerate(s) if d >= threshold] for s in triplets ]
        # Extract the triplets
        triplets = [[self.idx2tripl[i] for i in s] for s in indeces]
        # Add "proxy" triplets due to the fact that the network can't process void triplets
        for s in triplets:
            if s == []:
                s.append("('There', 'is', 'no triplet')")
        
        # Retrieve the graph and graph features
        graph, graph_feats = tripl2graph(triplets, self.feature_encoder, self.tokenizer)
        i_feats = self.img_encoder(img)
        graph, graph_feats = graph.to(img.device), graph_feats.to(img.device)
        graph_feats = self.dropout(self.graph_encoder(graph, graph_feats))
        # Mod feats with concatenation
        mod_feats = torch.cat([graph_feats, i_feats], dim=1)
        # Mod feats with weighted sum and main graph
        # mod_feats = graph_feats + ( i_feats * self.img_weight)
        # Mod feats with weighted sum and main Image
        # mod_feats = i_feats + ( graph_feats * self.img_weight)
        if self.decoder_type == 'linear':
            decoded_out = [d(mod_feats) for d in self.decoder]
        # Need to solve the problem with lstm and rnn for the labels
        if self.decoder_type == 'lstm':
            decoded_out = self.decoder(graph, mod_feats, labels, training)
        # if self.decoder_type == 'rnn':
        #     decoded_out = self.decoder(mod_feats, labels)
        return decoded_out, class_out

    def _loss(self, out, labels, vocab2idx, max_seq_len, device) -> torch.Tensor:
        batched_label = torch.vstack([_encode_seq_to_arr(label, vocab2idx, max_seq_len) for label in labels])
        return sum([nn.CrossEntropyLoss()(out[i], batched_label[:, i].to(device=device)) for i in range(max_seq_len)])/max_seq_len
    
    
# WIP

class FinetunedModel(nn.Module):
    '''
    Caption generation network (encoder-decoder)

    Args:
        feats_dim: dimension of the features
        max_seq_len: maximum tokens in a caption
        vocab2idx: dictionary for one hot encoding of tokens
        decoder: type of decoder (linear, lstm or rnn)
    '''
    def __init__(self, vocab2idx, img_dim, tripl2idx, decoder) -> None:
        super(FinetunedModel, self).__init__()
        self.decoder = torch.load(decoder)
        self.tripl_classifier = MultiHeadClassifier(img_dim, len(tripl2idx))
        self.idx2tripl = {v: k for k, v in tripl2idx.items()}
        self.feature_encoder = BertModel.from_pretrained("bert-base-uncased")
        self.tokenizer = BertTokenizer.from_pretrained("bert-base-uncased")
        self.vocab2idx = vocab2idx
        self.idx2vocab = {v: k for k, v in vocab2idx.items()}

    def forward(self, img):
        # Triplet classification
        triplets = self.tripl_classifier(img)
        # For multihead classifier
        triplets = triplets.reshape((triplets.shape[0], int(triplets.shape[1]/2), 2))
        class_out = triplets
        triplets = [[torch.argmax(logits).item() for logits in img] for img in triplets]
        # Extract indeces greater or equal than the threshold
        threshold = 0.5
        indeces = [[ i for i, d in enumerate(s) if d >= threshold] for s in triplets ]
        # Extract the triplets
        triplets = [[self.idx2tripl[i] for i in s] for s in indeces]
        # Add "proxy" triplets due to the fact that the network can't process void triplets
        for s in triplets:
            if s == []:
                s.append("('There', 'is', 'no triplet')")
        
        # Retrieve the graph and graph features
        graph, graph_feats = tripl2graph(triplets, self.feature_encoder, self.tokenizer)
        graph, graph_feats = graph.to(img.device), graph_feats.to(img.device)
        
        
        
        decoded_out = self.decoder(graph, graph_feats, img)
        
        
        return decoded_out, class_out

    def _loss(self, out, labels, vocab2idx, max_seq_len, device) -> torch.Tensor:
        batched_label = torch.vstack([_encode_seq_to_arr(label, vocab2idx, max_seq_len) for label in labels])
        return sum([nn.CrossEntropyLoss()(out[i], batched_label[:, i].to(device=device)) for i in range(max_seq_len)])/max_seq_len




if __name__=="__main__":
    model = TripletClassifier(224,10)
    dummy_img = torch.randn((5,3,224,224))
    out = model(dummy_img)