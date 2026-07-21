@router.callback_query(F.data.startswith("direct_item:"))
async def direct_item_handler(call: CallbackQuery, state: FSMContext):
    item_id = int(call.data.split(':')[1])
    from bot.database.methods.read import get_goods_info
    item = await get_goods_info(item_id)
    if not item:
        await safe_edit_or_send(call, localize("shop.item.not_found"), reply_markup=back("back_to_menu"))
        return
        
    await answer_callback_safe(call)
    await state.update_data(item_quantity=1, keypad_value='0', item_id=item_id, csrf_item=item['name'], item_back_data='menu')
    await _render_item_page(call, state, item['name'], back_data='menu', user_id=call.from_user.id)

