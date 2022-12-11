from utils import *
from torch.autograd import Variable
import argparse
import sys

import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torch.utils.data

import numpy as np
import torch
import matplotlib.pyplot as plt
from preprocess import get_question_matrix


def load_data(base_path="../data"):
    """ Load the data in PyTorch Tensor.

    :return: (zero_train_matrix, train_data, valid_data, test_data)
        WHERE:
        zero_train_matrix: 2D sparse matrix where missing entries are
        filled with 0.
        train_data: 2D sparse matrix
        valid_data: A dictionary {user_id: list,
        user_id: list, is_correct: list}
        test_data: A dictionary {user_id: list,
        user_id: list, is_correct: list}
    """
    train_matrix = load_train_sparse(base_path).toarray()
    valid_data = load_valid_csv(base_path)
    test_data = load_public_test_csv(base_path)

    zero_train_matrix = train_matrix.copy()
    # Fill in the missing entries to 0.
    zero_train_matrix[np.isnan(train_matrix)] = 0
    # Change to Float Tensor for PyTorch.
    zero_train_matrix = torch.FloatTensor(zero_train_matrix)
    train_matrix = torch.FloatTensor(train_matrix)

    return zero_train_matrix, train_matrix, valid_data, test_data


class AutoEncoder(nn.Module):
    def __init__(self, num_student):
        """ Initialize a class AutoEncoder.

        :param num_question: int
        :param k: int
        """
        super(AutoEncoder, self).__init__()

        self.g1 = nn.Linear(num_student, 256)
        self.g2 = nn.Linear(256, 128)
        self.g3 = nn.Linear(128, 64)
        self.h3 = nn.Linear(64, 128)
        self.h2 = nn.Linear(128, 256)
        self.h1 = nn.Linear(256, num_student)

    def get_weight_norm(self):
        """ Return ||W^1||^2 + ||W^2||^2.

        :return: float
        """
        g_w_norm = torch.norm(self.g1.weight, 2) ** 2 +\
                   torch.norm(self.g2.weight, 2) ** 2 +\
                   torch.norm(self.g3.weight, 2) ** 2
        h_w_norm = torch.norm(self.h1.weight, 2) ** 2 +\
                   torch.norm(self.h2.weight, 2) ** 2 +\
                   torch.norm(self.h3.weight, 2) ** 2
        return g_w_norm + h_w_norm

    def forward(self, inputs, question_info):
        """ Return a forward pass given inputs.

        :param inputs: user vector.
        :return: user vector.
        """
        x = self.g1(inputs)
        x = F.relu(x)
        x = self.g2(x)
        x = nn.Tanh()(x)
        x = self.g3(x)
        x = nn.Sigmoid()(x)

        y = self.h3(x)
        y = nn.Tanh()(y)
        y = self.h2(y)
        y = F.relu(y)
        y = self.h1(y)
        out = torch.sigmoid(y)

        return out


def train(model, lr, lamb, question_meta, train_data, zero_train_data, valid_data, num_epoch):
    """ Train the neural network, where the objective also includes
    a regularizer.

    :param model: Module
    :param lr: float
    :param lamb: float
    :param train_data: 2D FloatTensor
    :param zero_train_data: 2D FloatTensor
    :param valid_data: Dict
    :param num_epoch: int
    :return: None
    """
    device = (torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu'))
    model = model.to(device=device)

    # Tell PyTorch you are training the model.
    model.train()

    # Define optimizers and loss function.
    optimizer = optim.Adam(model.parameters(), lr=lr)
    num_student = train_data.shape[0]
    num_question = train_data.shape[1]
    num_subject = len(question_meta)

    train_lst = []
    valid_lst = []
    best_valid_acc = 0
    for epoch in range(0, num_epoch):
        train_loss = 0.

        for q_id in range(num_question):
            inputs = Variable(zero_train_data[:, q_id]).unsqueeze(0).to(device=device)
            q_info = question_meta[:, q_id].reshape(1, num_subject).to(device=device)
            target = inputs.clone()

            optimizer.zero_grad()
            output = model(inputs, q_info)

            # Mask the target to only compute the gradient of valid entries.
            nan_mask = np.isnan(train_data[:, q_id].unsqueeze(0).numpy())
            target[0][nan_mask] = output[0][nan_mask]

            # loss = torch.sum((output - target) ** 2.)
            loss = torch.sum((output - target) ** 2.) + model.get_weight_norm() * lamb * 0.5
            loss.backward()

            train_loss += loss.item()
            optimizer.step()

        valid_acc = evaluate(model, question_meta, zero_train_data, valid_data)
        if valid_acc > best_valid_acc:
            best_valid_acc = valid_acc
        train_lst.append(train_loss)
        valid_lst.append(valid_acc)
        print("Epoch: {} \tTraining Cost: {:.6f}\t "
              "Valid Acc: {} \tBest Valid Acc: {:.6f}".format(epoch + 1, train_loss, valid_acc,
                                                              best_valid_acc))
        sys.stdout.flush()
    return best_valid_acc
    #####################################################################
    #                       END OF YOUR CODE                            #
    #####################################################################


def evaluate(model, question_meta, train_data, valid_data):
    """ Evaluate the valid_data on the current model.

    :param model: Module
    :param train_data: 2D FloatTensor
    :param valid_data: A dictionary {user_id: list,
    question_id: list, is_correct: list}
    :return: float
    """
    device = (torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu'))
    model = model.to(device=device)
    # Tell PyTorch you are evaluating the model.
    model.eval()

    num_subject = len(question_meta)

    total = 0
    correct = 0

    for i, u in enumerate(valid_data["question_id"]):
        inputs = Variable(train_data[:, u]).unsqueeze(0).to(device=device)
        q_info = question_meta[:, u].reshape(1, num_subject).to(device=device)
        output = model(inputs, q_info)

        guess = output[0][valid_data["user_id"][i]].item() >= 0.5
        if guess == valid_data["is_correct"][i]:
            correct += 1
        total += 1
    return correct / float(total)


def predict_private_data(model, train_data, lr, lamb, num_epoch):
    device = (torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu'))

    # Load the private test dataset.
    private_test = load_private_test_csv("../data")
    predictions = []
    for i, q in enumerate(private_test["question_id"]):
        inputs = Variable(train_data[:, q]).unsqueeze(0).to(device=device)
        output = model(inputs, None)
        ratio = output[0][private_test["user_id"][i]].item()

        if ratio >= 0.5:
            predictions.append(1.)
        else:
            predictions.append(0.)

    private_test["is_correct"] = predictions
    # Save your predictions to make it ready to submit to Kaggle.
    save_private_test_csv(private_test, file_name=f'private_test_result_wo_{lr}_{lamb}_{num_epoch}.csv')


def main():
    print(f'no student info')

    np.random.seed(0)
    torch.manual_seed(0)

    zero_train_matrix, train_matrix, valid_data, test_data = load_data()
    question_matrix = torch.FloatTensor(get_question_matrix())

    num_student = train_matrix.shape[0]

    # read the beta value from the command line
    parser = argparse.ArgumentParser()
    parser.add_argument("-lr", "--lr", type=float, help="learning rate")
    parser.add_argument("-lamb", "--lamb", type=float, help="lambda")
    parser.add_argument("-num_epoch", "--num_epoch", type=int, help="number of epochs")
    args = parser.parse_args()
    lr = args.lr
    lamb = args.lamb
    num_epoch = args.num_epoch

    print('----------------------------------------------------------')
    print(f'lr={lr}, lamb={lamb}, num_epoch={num_epoch}')
    model = AutoEncoder(num_student)
    best_valid_acc = train(model, lr, lamb, question_matrix, train_matrix, zero_train_matrix, valid_data, num_epoch)
    test_acc = evaluate(model, question_matrix, zero_train_matrix, test_data)
    print(f'lr={lr}, lamb={lamb}, num_epoch={num_epoch}, test_acc={test_acc}')
    print('----------------------------------------------------------')

    predict_private_data(model, zero_train_matrix, lr, lamb, num_epoch)       

if __name__ == "__main__":
    main()
