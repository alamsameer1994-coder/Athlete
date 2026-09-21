from athlete_coach.coaching.nutrition import bmr_mifflin_st_jeor, calorie_and_macro_targets


def test_bmr_male():
    bmr = bmr_mifflin_st_jeor(weight_kg=75, height_cm=180, age=30, sex="male")
    assert round(bmr) == round(10 * 75 + 6.25 * 180 - 5 * 30 + 5)


def test_bmr_female():
    bmr = bmr_mifflin_st_jeor(weight_kg=60, height_cm=165, age=28, sex="female")
    assert round(bmr) == round(10 * 60 + 6.25 * 165 - 5 * 28 - 161)


def test_fat_loss_target_below_tdee():
    targets = calorie_and_macro_targets(weight_kg=75, tdee=2800, goal="fat_loss", aggressiveness="moderate")
    assert targets["target_calories"] < 2800
    assert targets["target_calories"] >= 2800 * 0.75  # floor respected


def test_muscle_gain_target_above_tdee():
    targets = calorie_and_macro_targets(weight_kg=75, tdee=2800, goal="muscle_gain", aggressiveness="moderate")
    assert targets["target_calories"] > 2800


def test_maintenance_equals_tdee():
    targets = calorie_and_macro_targets(weight_kg=75, tdee=2800, goal="maintenance")
    assert targets["target_calories"] == 2800


def test_macros_sum_to_roughly_target_calories():
    targets = calorie_and_macro_targets(weight_kg=75, tdee=2800, goal="maintenance")
    total = targets["protein_g"] * 4 + targets["fat_g"] * 9 + targets["carbs_g"] * 4
    assert abs(total - targets["target_calories"]) < 5
