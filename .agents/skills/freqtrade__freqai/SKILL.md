---
name: freqtrade__freqai
description: ML subsystem — feature engineering, prediction models, reinforcement learning, data management
triggers: [freqai, prediction model, machine learning, reinforcement learning, data kitchen]
---

# FreqAI (Machine Learning)

**Source**: freqtrade
**Category**: Domain

## When to use this skill
Using or extending the FreqAI machine learning subsystem: training prediction models, feature engineering, reinforcement learning environments, or managing training data.

## Key files and folders
- `/home/ricardo/github/freqtrade/freqtrade/freqai/freqai_interface.py` — `FreqaiInterface` integrating with the main bot loop
- `/home/ricardo/github/freqtrade/freqtrade/freqai/data_kitchen.py` — `FreqaiDataKitchen`: feature engineering, data labeling, scaling
- `/home/ricardo/github/freqtrade/freqtrade/freqai/data_drawer.py` — `FreqaiDataDrawer`: training data persistence and management
- `/home/ricardo/github/freqtrade/freqtrade/freqai/prediction_models/` — Model implementations: LightGBM, XGBoost, PyTorch, SKLearn
- `/home/ricardo/github/freqtrade/freqtrade/freqai/RL/` — Reinforcement learning: Stable-Baselines3 environments
- `/home/ricardo/github/freqtrade/freqtrade/freqai/torch/` — PyTorch trainer and model definitions
- `/home/ricardo/github/freqtrade/freqtrade/freqai/tensorboard/` — TensorBoard integration
- `/home/ricardo/github/freqtrade/freqtrade/freqai/base_models/` — Base model classes
- `/home/ricardo/github/freqtrade/freqtrade/freqai/utils.py` — FreqAI utilities

## Key concepts
- **Data Kitchen**: feature engineering pipeline — creates, labels, scales, and splits training data.
- **Data Drawer**: persistent storage and retrieval of training data, models, and metadata.
- **Prediction models**: pluggable — LightGBM (default), XGBoost, PyTorch Neural Net, SKLearn classifiers, or custom.
- **RL**: custom Gymnasium environments for training reinforcement learning agents via Stable-Baselines3.
- **Training modes**: live retraining (retrain on each new candle), periodic retraining, or backtest-only.

## Related skills
- See `.agents/skills/freqtrade__trading-engine` — the bot loop that invokes FreqAI
- See `.agents/skills/freqtrade__data-management` — data sources used by the data kitchen
