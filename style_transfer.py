'''we import the essential libraries
we are using pytorch as the parent library. We have enabled GPU functions for efficient performance. 
torch.nn is the neural network library, optim is to use SGD or adam optimiser
we import PIL for image processing and other image related stuff
torchvisions transforms are used to convert various datatypes into tensors and other such operations
'''
from __future__ import print_function
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from PIL import Image
import matplotlib.pyplot as plt
import torchvision.transforms as transforms
import torchvision.models as models
import copy
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(device) #Checking if the CuDA distribution works  and if it is up and running
'''we will first decide the parameters that will resize the image. The style image and the content image should be of the same size.'''
imsize = 512 if torch.cuda.is_available() else 128 #smalller size if cuda is unavailable since you don't want to overload your CPU

# the next line is where the core scaling of image takes place.
loader = transforms.Compose([
    transforms.Resize(imsize),  
    transforms.ToTensor()]) 
#note that the function takes the parameters of imsize 

def image_operation(image_name):
    image = Image.open(image_name)
    image = loader(image).unsqueeze(0) #unsqueeze artificially adds a dimension to the existing image
    #image.astype(torch.float) is the key idea here.
    #We perform operations and convert the result into a tensor and push it to the GPU as a torch.float
    return image.to(device, torch.float)

content_image = image_operation(r'D:\Downloads\Simran Patil (_simran2115) • Instagram photos and videos\simran.jpg')
style_image  = image_operation(r'D:\Downloads\Simran Patil (_simran2115) • Instagram photos and videos\dance.jpg')
unloader = transforms.ToPILImage()  # reconvert into PIL image

plt.ion()

def image_show(tensor, title=None):
    image = tensor.cpu().clone()  # we clone the tensor to not do changes on it
    image = image.squeeze(0)      # remove the fake batch dimension
    image = unloader(image)
    plt.imshow(image)
    if title is not None:
        plt.title(title)
    plt.pause(0.001) # pause a bit so that plots are updated


plt.figure()
image_show(style_image, title='Style Image')

plt.figure()
image_show(content_image, title='Content Image')

'''The content loss is a function that represents a weighted version of the content distance for an individual layer. 
The function takes the feature maps FXL of a layer L in a network processing input X and returns the weighted content distance wCL.DLC(X,C) between the image X and the content image C.
The feature maps of the content image(FCL) must be known by the function in order to calculate the content distance. 
We implement this function as a torch module with a constructor that takes FCL as an input. 
The distance ∥FXL−FCL∥2 is the mean square error between the two sets of feature maps, and can be computed using nn.MSELoss.
We will add this content loss module directly after the convolution layer(s) that are being used to compute the content distance. 
This way each time the network is fed an input image the content losses will be computed at the desired layers and because of auto grad, all the gradients will be computed.
Now, in order to make the content loss layer transparent we must define a forward method that computes the content loss and then returns the layer’s input. 
The computed loss is saved as a parameter of the module.'''

class ContentLoss(nn.Module):
    def __init__(self,target,):
         super(ContentLoss, self).__init__()
         # we 'detach' the target content from the tree used
         # to dynamically compute the gradient: this is a stated value,
         # not a variable. Otherwise the forward method of the criterion
         # will throw an error.
         self.target = target.detach()

    def forward(self, input):
        self.loss = F.mse_loss(input, self.target)
        return input
    def gram_matrix(input):
        a, b, c, d = input.size()  # a=batch size(=1)
        # b=number of feature maps
        # (c,d)=dimensions of a f. map (N=c*d)
        features = input.view(a * b, c * d)  # resise F_XL into \hat F_XL
        G = torch.mm(features, features.t())  # compute the gram product
        # we 'normalize' the values of the gram matrix
        # by dividing by the number of element in each feature maps.
        return G.div(a * b * c * d)

class StyleLoss(nn.Module):

    def __init__(self, target_feature):
        super(StyleLoss, self).__init__()
        self.target = ContentLoss.gram_matrix(target_feature).detach()

    def forward(self, input):
        G = ContentLoss.gram_matrix(input)
        self.loss = F.mse_loss(G, self.target)
        return input
    
cnn = models.vgg19(pretrained=True).features.to(device).eval()
cnn_normalization_mean = torch.tensor([0.485, 0.456, 0.406]).to(device)
cnn_normalization_std = torch.tensor([0.229, 0.224, 0.225]).to(device)

# create a module to normalize input image so we can easily put it in a
# nn.Sequential
class Normalization(nn.Module):
    def __init__(self, mean, std):
        super(Normalization, self).__init__()
        # .view the mean and std to make them [C x 1 x 1] so that they can
        # directly work with image Tensor of shape [B x C x H x W].
        # B is batch size. C is number of channels. H is height and W is width.
        self.mean = torch.tensor(mean).view(-1, 1, 1)
        self.std = torch.tensor(std).view(-1, 1, 1)

    def forward(self, img):
        # normalize img
        return (img - self.mean) / self.std
    
content_layers_default = ['conv_4']
style_layers_default = ['conv_1', 'conv_2', 'conv_3', 'conv_4', 'conv_5']

def get_style_model_and_losses(cnn, normalization_mean, normalization_std,
                               style_img, content_img,
                               content_layers=content_layers_default,
                               style_layers=style_layers_default):
    cnn = copy.deepcopy(cnn)

    # normalization module
    normalization = Normalization(normalization_mean, normalization_std).to(device)

    # just in order to have an iterable access to or list of content/syle
    # losses
    content_losses = []
    style_losses = []

    # assuming that cnn is a nn.Sequential, so we make a new nn.Sequential
    # to put in modules that are supposed to be activated sequentially
    model = nn.Sequential(normalization)

    i = 0  # increment every time we see a conv
    for layer in cnn.children():
        if isinstance(layer, nn.Conv2d):
            i += 1
            name = 'conv_{}'.format(i)
        elif isinstance(layer, nn.ReLU):
            name = 'relu_{}'.format(i)
            # The in-place version doesn't play very nicely with the ContentLoss
            # and StyleLoss we insert below. So we replace with out-of-place
            # ones here.
            layer = nn.ReLU(inplace=False)
        elif isinstance(layer, nn.MaxPool2d):
            name = 'pool_{}'.format(i)
        elif isinstance(layer, nn.BatchNorm2d):
            name = 'bn_{}'.format(i)
        else:
            raise RuntimeError('Unrecognized layer: {}'.format(layer.__class__.__name__))

        model.add_module(name, layer)

        if name in content_layers:
            # add content loss:
            target = model(content_img).detach()
            content_loss = ContentLoss(target)
            model.add_module("content_loss_{}".format(i), content_loss)
            content_losses.append(content_loss)

        if name in style_layers:
            # add style loss:
            target_feature = model(style_img).detach()
            style_loss = StyleLoss(target_feature)
            model.add_module("style_loss_{}".format(i), style_loss)
            style_losses.append(style_loss)

    # now we trim off the layers after the last content and style losses
    for i in range(len(model) - 1, -1, -1):
        if isinstance(model[i], ContentLoss) or isinstance(model[i], StyleLoss):
            break

    model = model[:(i + 1)]

    return model, style_losses, content_losses

input_img = content_image.clone()
# if you want to use white noise instead uncomment the below line:
# input_img = torch.randn(content_img.data.size(), device=device)

# add the original input image to the figure:
plt.figure()
#image_show(input_img, title='Input Image')
def get_input_optimizer(input_img):
    # this line to show that input is a parameter that requires a gradient
    optimizer = optim.LBFGS([input_img.requires_grad_()])
    return optimizer
def run_style_transfer(cnn, normalization_mean, normalization_std,
                       content_img, style_img, input_img, num_steps=300,
                       style_weight=1000000, content_weight=1):
    """Run the style transfer."""
    print('Building the style transfer model..')
    model, style_losses, content_losses = get_style_model_and_losses(cnn,
        normalization_mean, normalization_std, style_img, content_img)
    optimizer = get_input_optimizer(input_img)

    print('Optimizing..')
    run = [0]
    while run[0] <= num_steps:

        def closure():
            # correct the values of updated input image
            input_img.data.clamp_(0, 1)

            optimizer.zero_grad()
            model(input_img)
            style_score = 0
            content_score = 0

            for sl in style_losses:
                style_score += sl.loss
            for cl in content_losses:
                content_score += cl.loss

            style_score *= style_weight
            content_score *= content_weight

            loss = style_score + content_score
            loss.backward()

            run[0] += 1
            if run[0] % 50 == 0:
                print("run {}:".format(run))
                print('Style Loss : {:4f} Content Loss: {:4f}'.format(
                    style_score.item(), content_score.item()))
                print()

            return style_score + content_score

        optimizer.step(closure)

    # a last correction...
    input_img.data.clamp_(0, 1)

    return input_img
output = run_style_transfer(cnn, cnn_normalization_mean, cnn_normalization_std,
                            content_image, style_image, input_img)

plt.figure()
image_show(output, title='Output Image')

# sphinx_gallery_thumbnail_number = 4
plt.ioff()
plt.show()