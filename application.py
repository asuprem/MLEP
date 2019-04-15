# pylint: disable=no-value-for-parameter
import os, time, json, sys, pdb
from MLEPServer import MLEPLearningServer, MLEPPredictionServer
from utils import std_flush, readable_time, load_json
import click

from config.DataModel.BatchedLocal import BatchedLocal
from config.DataModel.StreamLocal import StreamLocal
from config.DataSet.PseudoJsonTweets import PseudoJsonTweets

"""
Arguments

python application.py experimentName [updateSchedule] [weightMethod] [selectMethod] [filterMethod] [kVal]

"""


@click.command()
@click.argument('experimentname')
@click.option('--update', type=int)
@click.option('--weights', type=click.Choice(["unweighted", "performance"]))
@click.option('--select', type=click.Choice(["train", "historical", "historical-new", "historical-updates","recent","recent-new","recent-updates"]))
@click.option('--filter', type=click.Choice(["no-filter", "top-k", "nearest"]))
@click.option('--kval', type=int)
def main(experimentname, update, weights, select, filter, kval):
    # We'll load thhe config file, make changes, and write a secondary file for experiments
    mlepConfig = load_json('./config/configuration/MLEPServer.json')
    if update is not None:
        mlepConfig["config"]['update_schedule'] = update
    if weights is not None:
        mlepConfig["config"]['weight_method'] = weights
    if select is not None:
        mlepConfig["config"]['select_method'] = select
    if filter is not None:
        mlepConfig["config"]['filter_select'] = filter
    if kval is not None:
        mlepConfig["config"]['k-val'] = kval
    
    PATH_TO_CONFIG_FILE = './config/configuration/ExperimentalConfig.json'
    with open(PATH_TO_CONFIG_FILE, 'w') as write_:
        write_.write(json.dumps(mlepConfig))

    
    # Where to save data:
    #pdb.set_trace()
    writePath = 'dataCollect.csv'
    savePath = open(writePath, 'a')
    savePath.write(experimentname + ',')
    
    internalTimer = 0

    # TODO --> sortDataTimes()
    # 
    data = []
    with open('data/data/2014_to_dec2018.json','r') as data_file:
        for line in data_file:
            data.append(json.loads(line.strip()))


    streamData = StreamLocal(data_source="data/data/2014_to_dec2018.json", data_mode="single", data_set_class=PseudoJsonTweets)


    augmentation = BatchedLocal(data_source='data/data/collectedIrrelevant.json', data_mode="single", data_set_class=PseudoJsonTweets)
    augmentation.load_by_class()
    
    trainingData = BatchedLocal(data_source='data/data/initialTrainingData.json', data_mode="single", data_set_class=PseudoJsonTweets)
    trainingData.load()

    """
    BatchedLocal.getData() --> return list of DataSet objects
    BatchedLocal.getLabels()

    """
    

    # Let's consider three data delivery models:
    #   - Batched
    #   - Streaming (single example)
    #   - Streaming batched (multiple examples at once)

    # We also have multiple data models - how a single piece of data is delivered:
    #   - TextPseudoJsonModel 
    #   - ImageModel
    #   - VideoModel
    #   - NumericModel
    # Each model has these required methods
    #   
    
    # Now we have the data
    MLEPLearner = MLEPLearningServer(PATH_TO_CONFIG_FILE)
    MLEPPredictor = MLEPPredictionServer()

    # Train with raw training data (for now)
    # Assumptions - there is a 'text' field; assume we have access to a w2v encoder

    # We'll pass a training data model...
    # datamodel is a streaming data model??? --> look at streaming in sci-kit multiflow

    MLEPLearner.initialTrain(traindata=trainingData)
    MLEPLearner.memoryTrack("default")
    std_flush("Completed training at", readable_time())
    MLEPLearner.addAugmentation(augmentation)

    # let's do something with it
    totalCounter = []
    mistakes = []
    while streamData.next():
        # Eventually, we need to move beyond literal time and into pure drift adaptivity
        # TODO ^^
        if internalTimer < streamData.getObject().getValue("timestamp"):
            internalTimer = streamData.getObject().getValue("timestamp")
            MLEPLearner.updateTime(internalTimer)

        classification = MLEPPredictor.classify(streamData.getObject(), MLEPLearner)
        totalCounter.append(1)
        if classification != streamData.getLabel():
            mistakes.append(1.0)
        else:
            mistakes.append(0.0)
        if len(totalCounter) % 1000 == 0 and len(totalCounter)>0:
            std_flush("Completed", len(totalCounter), " samples, with running error (past 100) of", sum(mistakes[-100:])/sum(totalCounter[-100:]))
        if len(totalCounter) % 100 == 0 and len(totalCounter)>0:
            savePath.write(str(sum(mistakes[-100:])/sum(totalCounter[-100:]))+',')

    
    # Perform data collection???
    savePath.write('\n')
    savePath.close()    

    MLEPLearner.shutdown()

    std_flush("\n-----------------------------\nCOMPLETED\n-----------------------------\n")


if __name__ == "__main__":
    main()