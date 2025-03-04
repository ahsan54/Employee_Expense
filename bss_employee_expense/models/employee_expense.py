from odoo import fields, models, api, _
from odoo.cli.scaffold import template
from odoo.exceptions import ValidationError
from datetime import timedelta
from odoo.exceptions import UserError
from datetime import datetime


class EmployeeExpense(models.Model):
    _name = 'expense.request'
    _rec_name = 'name'
    _inherit = ['mail.thread', 'mail.activity.mixin']  # Add mail.thread inheritance

    name = fields.Char(string="Expense Name", required=True, default=lambda self: _('New'))
    employee_id = fields.Many2one('hr.employee', string="Employee", required=True)
    amount = fields.Float(string="Expense Amount")
    currency_id = fields.Many2one('res.currency', string="Currency")
    expense_date = fields.Date(string="Expense Date")
    state = fields.Selection([('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'),
                              ('paid', 'Paid'), ('rejected', 'Rejected')], default='draft', string="Status")
    attachment_ids = fields.Many2many(
        'ir.attachment', 'expense_attachment_rel', 'res_id', 'attachment_id',
        string="Receipts & Documents", help="Attach expense receipts or related documents.")
    approved_by = fields.Many2one('res.users', string="Approved by")
    approved_date = fields.Date(string="Approved Date")

    expense_manager_id = fields.Many2one('res.users', string="Expense Manager",
                                         help="User responsible for approving expenses")
    payment_id = fields.Many2one('account.payment', string="Payment ID")
    rejection_reason = fields.Text(string="Rejection Reason", tracking=True)

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code('expense.request.sequence') or _('New')
            print(f'Generated Reference: {vals['name']}')
        return super(EmployeeExpense, self).create(vals)

    def action_submit(self):
        for expense in self:
            if expense.state == 'draft':
                expense.write({'state': 'submitted'})

        template_id = self.env.ref('bss_employee_expense.expense_request_email_to_manager_id').id

        ctx = {
            'default_model': 'expense.request',
            'default_res_ids': self.ids,  # Use self.ids for multiple records
            'default_template_id': template_id,
        }
        return {
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'target': 'new',
            'context': ctx,
        }

    def action_reset_to_draft(self):
        self.update({'state': 'draft'})

    def button_approve(self):
        self.write({'state': 'approved', 'approved_by': self.env.user.id, 'approved_date': datetime.now()})

        template_id = self.env.ref('bss_employee_expense.expense_request_email_to_finance_group_users_id')
        if template_id:
            template_id.send_mail(self.id, force_send=True)  # Sends email immediately

    def get_finance_group_emails(self):
        """Returns a comma-separated string of emails for users in the finance group."""
        finance_group = self.env.ref('bss_employee_expense.finance_approval_verify')
        finance_users = finance_group.users
        finance_emails = finance_users.mapped('email')  # Get email addresses
        return finance_emails

    def create_payment(self):
        return {
            'type': 'ir.actions.act_window',
            'name': f'Register Payment {self.id}',
            'res_model': 'account.payment',
            'view_mode': 'form',
            'view_id': False,  # Can specify a specific form view ID if needed
            'target': 'new',  # 'new' if you want a popup
            'context': {
                'default_expense_id': self.id,
                'default_partner_id': self.employee_id.id,  # Assigning employee as partner
                'default_amount': self.amount,  # Prefilling amount
                'default_payment_date': datetime.now().strftime('%Y-%m-%d'),  # Prefilling today's date
                'default_journal_id': self.env['account.journal'].search([('type', '=', 'bank')], limit=1).id,
                # Getting bank journal
            },
        }

    def action_reject(self):
        """ Manager rejects the expense and sends an email to the employee """
        self.ensure_one()

        # Get the employee email
        email_to = self.employee_id.private_email
        if not email_to:
            raise UserError("Employee does not have a private email set.")

        self.write({'state': 'rejected'})

        # Find the email template
        template = self.env.ref('bss_employee_expense.expense_request_email_to_employee_id')

        # Send email
        template.send_mail(self.id, force_send=True)

        # Log message in Chatter
        self.message_post(body="Expense Rejected. Notification sent to Employee.")


class AccountPaymentInherit(models.Model):
    _inherit = 'account.payment'

    expense_id = fields.Many2one('expense.request', string="Expense ID")

    def action_post(self):
        res = super(AccountPaymentInherit, self).action_post()
        search_expense = self.env['expense.request'].search([('id', '=', self.expense_id.id)])
        if search_expense:
            search_expense.write({'payment_id': self.id, 'state': 'paid'})
