def get_direct_subcategories(parent_id, all_cats):
    """زیرمجموعه‌های مستقیم یک دسته را برمی‌گرداند"""
    return [cat['id'] for cat in all_cats if cat['parent_id'] == parent_id]

def get_all_subcategories(parent_id, all_cats):
    """همه زیرمجموعه‌های یک دسته (بازگشتی)"""
    result = []
    direct = get_direct_subcategories(parent_id, all_cats)
    result.extend(direct)
    for sub_id in direct:
        result.extend(get_all_subcategories(sub_id, all_cats))
    return result

def get_selected_category_ids(parsed_selection, all_cats):
    """
    خروجی: لیست ID دسته‌هایی که باید محصولاتشان جمع‌آوری شود
    """
    selected_ids = set()
    for block in parsed_selection:
        for sel in block['selections']:
            if sel['type'] == 'all_subcats':
                # فقط زیرمجموعه‌های مستقیم همین دسته
                selected_ids.update(get_direct_subcategories(sel['id'], all_cats))
            elif sel['type'] == 'only_products':
                # فقط محصولات همین دسته
                selected_ids.add(sel['id'])
            elif sel['type'] == 'all_subcats_and_products':
                # همه زیرمجموعه‌های مستقیم و همه محصولات آن‌ها
                subcats = get_direct_subcategories(sel['id'], all_cats)
                selected_ids.update(subcats)
                for sub_id in subcats:
                    selected_ids.update(get_all_subcategories(sub_id, all_cats))
    return list(selected_ids)
