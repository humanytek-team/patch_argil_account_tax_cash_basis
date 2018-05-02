# -*- coding: utf-8 -*-
###############################################################################
#
#    Odoo, Open Source Management Solution
#    Copyright (C) 2017 Humanytek (<www.humanytek.com>).
#    Rubén Bravo <rubenred18@gmail.com>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################


from openerp import models, api, _, release

if release.major_version == "9.0":
    from openerp.osv import fields
elif release.major_version in ("10.0","11.0"):
    from odoo import fields
from openerp.exceptions import UserError, ValidationError
import openerp.addons.decimal_precision as dp
from openerp.tools import float_is_zero, float_compare
import json
import logging
_logger = logging.getLogger(__name__)


class AccountInvoice(models.Model):
    _inherit = "account.invoice"

    if release.major_version == "9.0":

        @api.v7
        def assign_outstanding_credit(self, cr, uid, id, credit_aml_id, context=None):
            invoice = self.browse(cr, uid, id, context)
            credit_aml = self.pool.get('account.move.line').browse(cr, uid, credit_aml_id, context=context)
            aml_to_reconcile = False

            # Revisamos si se requiere poliza para reclasificar el Anticipo de Cliente
            if (invoice.type == 'in_invoice' and \
                credit_aml.account_id.id != invoice.partner_id.property_account_payable_id.id and \
                credit_aml.account_id.id == (invoice.partner_id.property_account_supplier_advance_id and invoice.partner_id.property_account_supplier_advance_id.id or False)) \
               or \
                (invoice.type == 'out_invoice' and \
                credit_aml.account_id.id != credit_aml.partner_id.property_account_receivable_id.id and \
                credit_aml.account_id.id == (credit_aml.partner_id.property_account_customer_advance_id and credit_aml.partner_id.property_account_customer_advance_id.id or False)):

                if (invoice.type == 'out_invoice' and not invoice.partner_id.property_account_customer_advance_id) or\
                   (invoice.type == 'in_invoice' and not invoice.partner_id.property_account_supplier_advance_id):
                    raise UserError(_('The Partner has no Account defined for Customer / Supplier Advance Application. Please check.'))

                aml_obj = self.pool.get('account.move.line')
                move_obj = self.pool.get('account.move')
                #available_advance_amount_company_curr = credit_aml.amount_residual
                if credit_aml.currency_id: # Anticipo en ME
                    if credit_aml.currency_id == invoice.currency_id: # Moneda Anticipo == Moneda Factura
                        available_advance_amount_invoice_curr = credit_aml.amount_residual_currency
                    else: # Moneda Anticipo != Moneda Factura
                        available_advance_amount_invoice_curr = credit_aml.currency_id.with_context(date=credit_aml.date).compute(abs(credit_aml.amount_residual_currency), invoice.currency_id)
                elif invoice.currency_id == invoice.company_id.currency_id: # Moneda Anticipo MN == Moneda Factura MN
                    available_advance_amount_invoice_curr = credit_aml.amount_residual
                elif invoice.currency_id != invoice.company_id.currency_id: #  Moneda Anticipo MN, Moneda Factura ME
                    available_advance_amount_invoice_curr = credit_aml.company_id.currency_id.with_context(date=credit_aml.date).compute(abs(credit_aml.amount_residual), invoice.currency_id)

                if float_is_zero(available_advance_amount_invoice_curr, precision_rounding=invoice.currency_id.rounding):
                    available_advance_amount_invoice_curr = 0.0

                # Calculamos el porcentaje del Anticipo a aplicar a la factura
                factor = available_advance_amount_invoice_curr and (invoice.residual / available_advance_amount_invoice_curr) or 0.0
                if abs(factor) > 1.0:
                    factor = 1.0 * (available_advance_amount_invoice_curr >= 0 and 1 or -1)

                advance_amount_mn = abs(factor * credit_aml.amount_residual)
                advance_currency = False
                if credit_aml.currency_id:
                    advance_amount_me = abs(factor * credit_aml.amount_residual_currency)
                    advance_currency = credit_aml.currency_id
                else:
                    advance_amount_me = 0.0
                    if invoice.currency_id != invoice.company_id.currency_id:
                        advance_amount_me = abs(credit_aml.company_id.currency_id.with_context(date=credit_aml.date).compute(abs(credit_aml.amount_residual), invoice.currency_id))
                        advance_currency = invoice.currency_id

                journal_id = self.pool.get('account.journal').search(cr, uid, [('advance_application_journal','=',1)])
                if not journal_id:
                    raise UserError(_('There is no Journal defined for Customer / Supplier Advance Application. Please check.'))

                move_dict = {
                    'date'      : fields.date.context_today(self, cr, uid, context),
                    'ref'       : _('Pre-paid Application to Invoice: %s') % ((invoice.type=='out_invoice' and invoice.number or invoice.reference)),
                    'narration' : _('Pre-paid Application to Invoice: %s') % ((invoice.type=='out_invoice' and invoice.number or invoice.reference)),
                    'company_id': invoice.company_id.id,
                    'journal_id': journal_id[0],
                    }
                # Creamos la partida para la cuenta de Cliente / Proveedor
                aml_dict_partner = credit_aml.copy_data()[0]
                aml_dict_partner.update({
                    'name'           : _('Pre-paid Application to Invoice: %s') % (invoice.reference or invoice.number),
                    'account_id'     : invoice.account_id.id,
                    'date_maturity'  : fields.date.context_today(self, cr, uid, context),
                    'debit'          : invoice.type=='in_invoice' and advance_amount_mn or 0,
                    'credit'         : invoice.type=='out_invoice' and advance_amount_mn or 0,
                    'currency_id'    : advance_currency and advance_currency.id or False,
                    'amount_currency': (advance_amount_me and ((invoice.type=='in_invoice' and advance_amount_me) or (invoice.type=='out_invoice' and -advance_amount_me) or False)) or False,
                    'partner_id'     : invoice.partner_id.id,
                })
                aml_dict_advance = aml_dict_partner.copy()
                aml_dict_advance.update({
                    'account_id': (invoice.type=='in_invoice' and invoice.partner_id.property_account_supplier_advance_id.id) or \
                                  (invoice.type=='out_invoice' and invoice.partner_id.property_account_customer_advance_id.id),
                    'debit'     : aml_dict_partner['credit'],
                    'credit'    : aml_dict_partner['debit'],
                    'amount_currency': aml_dict_partner['amount_currency'] and -aml_dict_partner['amount_currency'] or 0.0,
                })
                ###################################################
                ###################################################
                fc_currency_id = credit_aml.currency_id and credit_aml.currency_id.id or credit_aml.company_id.currency_id.id
                lines = []
                factor_base = available_advance_amount_invoice_curr and (available_advance_amount_invoice_curr / invoice.residual) or 0.0
                factor_base2 = available_advance_amount_invoice_curr and (available_advance_amount_invoice_curr / invoice.amount_total) or 0.0
                if abs(factor_base) > 1.0:
                    factor_base = 1.0
                    factor_base2 = invoice.residual / invoice.amount_total
                for inv_line_tax in invoice.tax_line_ids.filtered(lambda r: r.tax_id.use_tax_cash_basis==True):
                    src_account_id = inv_line_tax.tax_id.account_id.id
                    dest_account_id = inv_line_tax.tax_id.tax_cash_basis_account.id
                    if not (src_account_id and dest_account_id):
                        raise UserError(_("Tax %s is not properly configured, please check." % (inv_line_tax.tax_id.name)))
                    mi_company_curr_orig = 0.0
                    for move_line in invoice.move_id.line_ids:
                        if move_line.account_id.id == inv_line_tax.tax_id.account_id.id:
                            mi_company_curr_orig = (move_line.debit + move_line.credit) * factor_base2 * (inv_line_tax.tax_id.amount >= 0 and 1.0 or -1.0)
                            mib_company_curr_orig = round(move_line.amount_base * factor_base2, 2)
                    if mi_company_curr_orig:
                        mi_invoice = inv_line_tax.amount * factor_base2 * factor
                        mib_invoice = mib_company_curr_orig / (mi_company_curr_orig / mi_invoice)
                        #################################
                        if ((invoice.type=='out_invoice' and inv_line_tax.tax_id.amount >= 0.0) or \
                                     (invoice.type=='in_invoice' and inv_line_tax.tax_id.amount < 0.0)):
                            debit = round(abs(mi_company_curr_orig),2)
                            credit = 0
                        elif ((invoice.type=='in_invoice' and inv_line_tax.tax_id.amount >= 0.0) or \
                                     (invoice.type=='out_invoice' and inv_line_tax.tax_id.amount < 0.0)):
                            debit = 0
                            credit = round(mi_company_curr_orig,2)

                        #################################
                        line2 = {
                                'name'            : inv_line_tax.tax_id.name + ((_(" - Fact: ") + (invoice.type=='out_invoice' and invoice.number or invoice.reference)) or 'N/A'),
                                'partner_id'      : invoice.partner_id.id,
                                'debit'           : debit,
                                'credit'          : credit,
                                'account_id'      : src_account_id,
                                'tax_id_secondary': inv_line_tax.tax_id.id,
                                'analytic_account_id': False,
                                'amount_base'     : abs(mib_company_curr_orig),
                            }

                        line1 = line2.copy()
                        line3 = {}
                        xparam = self.pool.get('ir.config_parameter').get_param(cr, uid, 'tax_amount_according_to_currency_exchange_on_payment_date', context=context)[0]
                        if not xparam == "1" or (invoice.company_id.currency_id.id == fc_currency_id == invoice.currency_id.id):
                            line1.update({
                                'name'        : inv_line_tax.tax_id.name + ((_(" - Fact: ") + (invoice.type=='out_invoice' and invoice.number or invoice.reference)) or 'N/A'),
                                'account_id'  : dest_account_id,
                                'debit'       : line2['credit'],
                                'credit'      : line2['debit'],
                                'amount_base' : line2['amount_base'],
                                })
                        elif xparam == "1":
                            monto_base = round((inv_line_tax.tax_id.amount and advance_amount_mn \
                                                        / (1.0 + (inv_line_tax.tax_id.amount / 100)) or (factor_base2 * inv_line_tax.amount_base_company_curr)), 2)
                            monto_a_reclasificar = round(inv_line_tax.tax_id.amount and monto_base * (inv_line_tax.tax_id.amount / 100) or 0.0,2)

                            line1.update({
                                'name': inv_line_tax.tax_id.name + ((_(" - Fact: ") + (invoice.type=='out_invoice' and invoice.number or invoice.reference)) or 'N/A'),
                                'debit': line2['credit'] and abs(monto_a_reclasificar) or 0.0,
                                'credit': line2['debit'] and abs(monto_a_reclasificar) or 0.0,
                                'account_id': dest_account_id,
                                'amount_base' : abs(monto_base),
                                })

                            if (round(mi_company_curr_orig, 2) - round(monto_a_reclasificar,2)):
                                amount_diff =  (round(abs(mi_company_curr_orig),2) - round(abs(monto_a_reclasificar),2)) * \
                                                (inv_line_tax.tax_id.amount >= 0 and 1.0 or -1.0)
                                line3 = {
                                    'name': _('Diferencia de ') + inv_line_tax.tax_id.name + (invoice and (_(" - Fact: ") + (invoice.type=='out_invoice' and invoice.number or invoice.reference)) or 'N/A'),
                                    'partner_id': invoice.partner_id.id,
                                    'debit': ((amount_diff < 0 and invoice.type=='out_invoice') or (amount_diff >= 0 and invoice.type=='in_invoice')) and abs(amount_diff) or 0.0,
                                    'credit': ((amount_diff < 0 and invoice.type=='in_invoice') or (amount_diff >= 0 and invoice.type=='out_invoice')) and abs(amount_diff) or 0.0,
                                    'account_id': (amount_diff < 0 ) and invoice.company_id.income_currency_exchange_account_id.id or invoice.company_id.expense_currency_exchange_account_id.id,
                                    'analytic_account_id': False,
                                    }
                            #else:
                            #    line3 = {}
                        lines += line3 and [(0,0,line1),(0,0,line2),(0,0,line3)] or [(0,0,line1),(0,0,line2)]

                lines += [(0,0, aml_dict_partner),(0,0, aml_dict_advance)]


                ###################################################
                ###################################################

                move_dict.update({'line_ids': lines})
                move_id = move_obj.create(cr, uid, move_dict)
                move_obj.post(cr, uid, [move_id])
                move = move_obj.browse(cr, uid, [move_id], context)
                aml_to_reconcile_advance = move.line_ids[0]
                # Creamos la partida para "descargar" la cuenta de Anticipo de Cliente / Proveedor
                aml_to_reconcile = move.line_ids[1]
                res = self.pool.get('account.move.line').reconcile(cr, uid, [aml_to_reconcile_advance.id, credit_aml.id], context=context)
            if aml_to_reconcile: # Se aplico Anticipo
                return self.browse(cr, uid, id, context={'active_ids': [id]}).register_payment(aml_to_reconcile)
            else:
                if not credit_aml.currency_id and invoice.currency_id != invoice.company_id.currency_id:
                    credit_aml.with_context(allow_amount_currency=True).write({
                        'amount_currency': invoice.company_id.currency_id.with_context(date=credit_aml.date).compute(credit_aml.balance, invoice.currency_id),
                        'currency_id': invoice.currency_id.id})
                if credit_aml.payment_id:
                    credit_aml.payment_id.write({'invoice_ids': [(4, id, None)]})
                return invoice.register_payment(credit_aml)

    elif release.major_version in ("10.0","11.0"):

        @api.multi
        def assign_outstanding_credit(self, credit_aml_id):
            self.ensure_one()
            invoice = self
            credit_aml = self.env['account.move.line'].browse(credit_aml_id)
            aml_to_reconcile = False

            # Revisamos si se requiere poliza para reclasificar el Anticipo de Cliente
            if (invoice.type == 'in_invoice' and \
                credit_aml.account_id.id != invoice.partner_id.property_account_payable_id.id and \
                credit_aml.account_id.id == (invoice.partner_id.property_account_supplier_advance_id and invoice.partner_id.property_account_supplier_advance_id.id or False)) \
               or \
                (invoice.type == 'out_invoice' and \
                credit_aml.account_id.id != credit_aml.partner_id.property_account_receivable_id.id and \
                credit_aml.account_id.id == (credit_aml.partner_id.property_account_customer_advance_id and credit_aml.partner_id.property_account_customer_advance_id.id or False)):

                if (invoice.type == 'out_invoice' and not invoice.partner_id.property_account_customer_advance_id) or\
                   (invoice.type == 'in_invoice' and not invoice.partner_id.property_account_supplier_advance_id):
                    raise UserError(_('The Partner has no Account defined for Customer / Supplier Advance Application. Please check.'))

                aml_obj = self.env['account.move.line']
                move_obj = self.env['account.move']
                #available_advance_amount_company_curr = credit_aml.amount_residual
                if credit_aml.currency_id: # Anticipo en ME
                    if credit_aml.currency_id == invoice.currency_id: # Moneda Anticipo == Moneda Factura
                        available_advance_amount_invoice_curr = credit_aml.amount_residual_currency
                    else: # Moneda Anticipo != Moneda Factura
                        available_advance_amount_invoice_curr = credit_aml.currency_id.with_context(date=credit_aml.date).compute(abs(credit_aml.amount_residual_currency), invoice.currency_id)
                elif invoice.currency_id == invoice.company_id.currency_id: # Moneda Anticipo MN == Moneda Factura MN
                    available_advance_amount_invoice_curr = credit_aml.amount_residual
                elif invoice.currency_id != invoice.company_id.currency_id: #  Moneda Anticipo MN, Moneda Factura ME
                    available_advance_amount_invoice_curr = credit_aml.company_id.currency_id.with_context(date=credit_aml.date).compute(abs(credit_aml.amount_residual), invoice.currency_id)

                if float_is_zero(available_advance_amount_invoice_curr, precision_rounding=invoice.currency_id.rounding):
                    available_advance_amount_invoice_curr = 0.0

                # Calculamos el porcentaje del Anticipo a aplicar a la factura
                factor = available_advance_amount_invoice_curr and (invoice.residual / available_advance_amount_invoice_curr) or 0.0
                if abs(factor) > 1.0:
                    factor = 1.0 * (available_advance_amount_invoice_curr >= 0 and 1 or -1)

                advance_amount_mn = abs(factor * credit_aml.amount_residual)
                advance_currency = False
                if credit_aml.currency_id:
                    advance_amount_me = abs(factor * credit_aml.amount_residual_currency)
                    advance_currency = credit_aml.currency_id
                else:
                    advance_amount_me = 0.0
                    if invoice.currency_id != invoice.company_id.currency_id:
                        advance_amount_me = credit_aml.company_id.currency_id.with_context(date=credit_aml.date).compute(abs(credit_aml.amount_residual), invoice.currency_id)
                        advance_currency = invoice.currency_id


                journal_id = self.env['account.journal'].search([('advance_application_journal','=',1)], limit=1)
                if not journal_id:
                    raise UserError(_('There is no Journal defined for Customer / Supplier Advance Application. Please check.'))

                move_dict = {
                    'date'      : fields.Date.context_today(self),
                    'ref'       : _('Pre-paid Application to Invoice: %s') % ((invoice.type=='out_invoice' and invoice.number or invoice.reference)),
                    'narration' : _('Pre-paid Application to Invoice: %s') % ((invoice.type=='out_invoice' and invoice.number or invoice.reference)),
                    'company_id': invoice.company_id.id,
                    'journal_id': journal_id.id,
                    }
                # Creamos la partida para la cuenta de Cliente / Proveedor
                aml_dict_partner = credit_aml.copy_data()[0]
                aml_dict_partner.update({
                    'name'           : _('Pre-paid Application to Invoice: %s') % (invoice.reference or invoice.number),
                    'account_id'     : invoice.account_id.id,
                    'date_maturity'  : fields.Date.context_today(self),
                    'debit'          : invoice.type=='in_invoice' and advance_amount_mn or 0,
                    'credit'         : invoice.type=='out_invoice' and advance_amount_mn or 0,
                    'currency_id'    : advance_currency and advance_currency.id or False,
                    'amount_currency': (advance_amount_me and ((invoice.type=='in_invoice' and advance_amount_me) or (invoice.type=='out_invoice' and -advance_amount_me) or False)) or False,
                    'partner_id'     : invoice.partner_id.id,
                })
                aml_dict_advance = aml_dict_partner.copy()
                aml_dict_advance.update({
                    'account_id': (invoice.type=='in_invoice' and invoice.partner_id.property_account_supplier_advance_id.id) or \
                                  (invoice.type=='out_invoice' and invoice.partner_id.property_account_customer_advance_id.id),
                    'debit'     : aml_dict_partner['credit'],
                    'credit'    : aml_dict_partner['debit'],
                    'amount_currency': aml_dict_partner['amount_currency'] and -aml_dict_partner['amount_currency'] or 0.0,
                })
                ###################################################
                ###################################################
                fc_currency_id = credit_aml.currency_id and credit_aml.currency_id.id or credit_aml.company_id.currency_id.id
                lines = []
                factor_base = available_advance_amount_invoice_curr and (available_advance_amount_invoice_curr / invoice.residual) or 0.0
                factor_base2 = available_advance_amount_invoice_curr and (available_advance_amount_invoice_curr / invoice.amount_total) or 0.0
                if abs(factor_base) > 1.0:
                    factor_base = 1.0
                    factor_base2 = invoice.residual / invoice.amount_total
                for inv_line_tax in invoice.tax_line_ids.filtered(lambda r: r.tax_id.use_tax_cash_basis==True):
                    src_account_id = inv_line_tax.tax_id.account_id.id
                    dest_account_id = inv_line_tax.tax_id.tax_cash_basis_account.id
                    if not (src_account_id and dest_account_id):
                        raise UserError(_("Tax %s is not properly configured, please check." % (inv_line_tax.tax_id.name)))
                    for move_line in invoice.move_id.line_ids:
                        if move_line.account_id.id == inv_line_tax.tax_id.account_id.id:
                            mi_company_curr_orig = (move_line.debit + move_line.credit) * factor_base2 * (inv_line_tax.tax_id.amount >= 0 and 1.0 or -1.0)
                            mib_company_curr_orig = round(move_line.amount_base * factor_base2, 2)
                    #mi_invoice = inv_line_tax.amount * factor_base2
                    #mib_invoice = mib_company_curr_orig / (mi_company_curr_orig / mi_invoice)
                    #################################
                    if ((invoice.type=='out_invoice' and inv_line_tax.tax_id.amount >= 0.0) or \
                                 (invoice.type=='in_invoice' and inv_line_tax.tax_id.amount < 0.0)):
                        debit = round(abs(mi_company_curr_orig),2)
                        credit = 0
                    elif ((invoice.type=='in_invoice' and inv_line_tax.tax_id.amount >= 0.0) or \
                                 (invoice.type=='out_invoice' and inv_line_tax.tax_id.amount < 0.0)):
                        debit = 0
                        credit = round(abs(mi_company_curr_orig),2)

                    #################################
                    line2 = {
                            'name'            : inv_line_tax.tax_id.name + ((_(" - Fact: ") + (invoice.type=='out_invoice' and invoice.number or invoice.reference)) or 'N/A'),
                            'partner_id'      : invoice.partner_id.id,
                            'debit'           : debit,
                            'credit'          : credit,
                            'account_id'      : src_account_id,
                            'tax_id_secondary': inv_line_tax.tax_id.id,
                            'analytic_account_id': False,
                            'amount_base'     : abs(mib_company_curr_orig),
                        }

                    line1 = line2.copy()
                    line3 = {}
                    xparam = self.env['ir.config_parameter'].get_param('tax_amount_according_to_currency_exchange_on_payment_date')[0]
                    if not xparam == "1" or (invoice.company_id.currency_id.id == fc_currency_id == invoice.currency_id.id):
                        line1.update({
                            'name'        : inv_line_tax.tax_id.name + ((_(" - Fact: ") + (invoice.type=='out_invoice' and invoice.number or invoice.reference)) or 'N/A'),
                            'account_id'  : dest_account_id,
                            'debit'       : line2['credit'],
                            'credit'      : line2['debit'],
                            'amount_base' : line2['amount_base'],
                            })
                    elif xparam == "1":
                        monto_base = round((inv_line_tax.tax_id.amount and advance_amount_mn \
                                                    / (1.0 + (inv_line_tax.tax_id.amount / 100)) or (factor_base2 * inv_line_tax.amount_base_company_curr)), 2)
                        monto_a_reclasificar = round(inv_line_tax.tax_id.amount and monto_base * (inv_line_tax.tax_id.amount / 100) or 0.0,2)

                        line1.update({
                            'name': inv_line_tax.tax_id.name + ((_(" - Fact: ") + (invoice.type=='out_invoice' and invoice.number or invoice.reference)) or 'N/A'),
                            'debit': line2['credit'] and abs(monto_a_reclasificar) or 0.0,
                            'credit': line2['debit'] and abs(monto_a_reclasificar) or 0.0,
                            'account_id': dest_account_id,
                            'amount_base' : abs(monto_base),
                            })

                        if (round(mi_company_curr_orig, 2) - round(monto_a_reclasificar,2)):
                            amount_diff =  (round(abs(mi_company_curr_orig),2) - round(abs(monto_a_reclasificar),2)) * \
                                            (inv_line_tax.tax_id.amount >= 0 and 1.0 or -1.0)
                            line3 = {
                                'name': _('Diferencia de ') + inv_line_tax.tax_id.name + (invoice and (_(" - Fact: ") + (invoice.type=='out_invoice' and invoice.number or invoice.reference)) or 'N/A'),
                                'partner_id': invoice.partner_id.id,
                                'debit': ((amount_diff < 0 and invoice.type=='out_invoice') or (amount_diff >= 0 and invoice.type=='in_invoice')) and abs(amount_diff) or 0.0,
                                'credit': ((amount_diff < 0 and invoice.type=='in_invoice') or (amount_diff >= 0 and invoice.type=='out_invoice')) and abs(amount_diff) or 0.0,
                                'account_id': (amount_diff < 0 ) and invoice.company_id.income_currency_exchange_account_id.id or invoice.company_id.expense_currency_exchange_account_id.id,
                                'analytic_account_id': False,
                                }
                        #else:
                        #    line3 = {}
                    lines += line3 and [(0,0,line1),(0,0,line2),(0,0,line3)] or [(0,0,line1),(0,0,line2)]
                lines += [(0,0, aml_dict_partner),(0,0, aml_dict_advance)]

                #for line in lines:
                #    print "line: ", line
                #raise UserError('Pausa')
                ###################################################
                ###################################################
                move_dict.update({'line_ids': lines})

                _logger.info('MOOOOOOOOVEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE')
                move = move_obj.create(move_dict)
                move.post()
                _logger.info(move)
                _logger.info(len(move.line_ids))
                _logger.info('LIIIIIIINEEEEEEEEEEEEE')
                ###
                aml_to_reconcile_advance = move.line_ids[0]
                # Creamos la partida para "descargar" la cuenta de Anticipo de Cliente / Proveedor
                aml_to_reconcile = move.line_ids[1]
                if invoice.type == 'out_invoice' and move.line_ids[0].debit <= 0:
                    aml_to_reconcile_advance = move.line_ids[0]
                    aml_to_reconcile = move.line_ids[1]
                ###
                _logger.info(aml_to_reconcile_advance.credit)
                _logger.info(aml_to_reconcile_advance.debit)
                _logger.info(aml_to_reconcile_advance.tax_id_secondary)
                _logger.info(aml_to_reconcile.credit)
                _logger.info(aml_to_reconcile.debit)
                _logger.info(aml_to_reconcile.tax_id_secondary)
                _logger.info(credit_aml.debit)
                _logger.info(credit_aml.credit)
                _logger.info(credit_aml.tax_id_secondary)
                (aml_to_reconcile_advance + credit_aml).reconcile()
                _logger.info('OOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOO')
            if aml_to_reconcile: # Se aplico Anticipo
                ###
                if credit_aml.payment_id:
                    credit_aml.payment_id.write({'invoice_ids': [(4, self.id, None)]})
                #return invoice.register_payment(credit_aml)
                ###
                #raise UserError(_('1'))
                return self.register_payment(aml_to_reconcile)
            else:
                if not credit_aml.currency_id and invoice.currency_id != invoice.company_id.currency_id:
                    credit_aml.with_context(allow_amount_currency=True).write({
                        'amount_currency': invoice.company_id.currency_id.with_context(date=credit_aml.date).compute(credit_aml.balance, invoice.currency_id),
                        'currency_id': invoice.currency_id.id})
                if credit_aml.payment_id:
                    credit_aml.payment_id.write({'invoice_ids': [(4, self.id, None)]})
                #raise UserError(_('2'))
                return invoice.register_payment(credit_aml)

